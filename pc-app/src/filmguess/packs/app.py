"""Editor de packs: una web local, aparte del juego, para crear y retocar packs.

Se arranca con ``arrancar.bat packs``. Solo escucha en el propio PC y no necesita ni
Netflix ni el móvil: lee el catálogo y escribe en ``data/packs/``.
"""

from __future__ import annotations

import threading
import webbrowser
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.catalog.search import search_movies
from filmguess.catalog.seed import install_seed
from filmguess.catalog.service import db_path
from filmguess.catalog.tmdb import poster_url
from filmguess.config import AppConfig
from filmguess.packs.store import (
    DEFAULT_KNOWN_RATIO,
    MAX_NAME_LENGTH,
    PACKS_DIRNAME,
    Pack,
    PackError,
    PackStore,
    build_pack,
)
from filmguess.pairing.network import is_localhost
from filmguess.webfiles import WEB_DIR

PACKS_PAGE = WEB_DIR / "packs" / "index.html"
DEFAULT_PORT = 8766
PAGE_SIZE = 60

ERROR_STATUS: dict[str, int] = {
    "solo_local": 403,
    "pack_no_encontrado": 404,
    "pack_repetido": 409,
    "pack_invalido": 422,
    "peticion_invalida": 422,
}


class NewPack(BaseModel):
    """Cuerpo de ``POST /api/packs``."""

    nombre: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class PackBody(BaseModel):
    """Cuerpo de ``PUT /api/packs/{slug}``."""

    nombre: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    conocidas: list[int] = Field(default_factory=list)
    desconocidas: list[int] = Field(default_factory=list)
    proporcionConocidas: float = Field(default=DEFAULT_KNOWN_RATIO, ge=0, le=1)


def movie_card(movie: Movie) -> dict[str, Any]:
    """Datos de una película para la cuadrícula del editor.

    Args:
        movie: Película.

    Returns:
        ``{netflixId, titulo, tituloOriginal, anio, poster}``.
    """
    return {
        "netflixId": movie.netflix_id,
        "titulo": movie.display_title,
        "tituloOriginal": movie.original_title,
        "anio": movie.year,
        "poster": poster_url(movie.poster_path),
    }


def pack_summary(pack: Pack) -> dict[str, Any]:
    """Resumen de un pack para la lista.

    Args:
        pack: Pack.

    Returns:
        Identificador, nombre, recuentos y proporción.
    """
    return {
        "id": pack.slug,
        "nombre": pack.name,
        "conocidas": len(pack.known),
        "desconocidas": len(pack.unknown),
        "proporcionConocidas": pack.known_ratio,
    }


def create_packs_app(db: CatalogDB, store: PackStore) -> FastAPI:
    """Crea la aplicación del editor de packs.

    Args:
        db: Catálogo (solo se lee).
        store: Almacén de packs.

    Returns:
        La aplicación FastAPI.
    """
    app = FastAPI(title="Packs · Adivina la Peli", docs_url=None, redoc_url=None, openapi_url=None)

    def error(code: str, message: str) -> JSONResponse:
        body = {"ok": False, "error": code, "mensaje": message}
        return JSONResponse(body, ERROR_STATUS.get(code, 500))

    @app.exception_handler(PackError)
    async def pack_error(_: Request, exc: PackError) -> JSONResponse:
        return error(exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error("peticion_invalida", "Faltan datos o no son válidos.")

    async def require_localhost(request: Request) -> None:
        if not is_localhost(request.client.host if request.client else None):
            raise PackError("solo_local", "Esta página solo se puede abrir en el propio PC.")

    async def require_editor(x_packs: Annotated[str | None, Header()] = None) -> None:
        # Una cabecera propia obliga al navegador a pedir permiso (CORS) desde otras webs,
        # y este servidor no lo concede: solo esta página puede cambiar los packs.
        if x_packs != "1":
            raise PackError("solo_local", "Los packs solo se cambian desde el editor.")

    local = [Depends(require_localhost)]
    write = [Depends(require_localhost), Depends(require_editor)]

    def detail(pack: Pack) -> dict[str, Any]:
        known = set(pack.known)
        movies = db.get_many([*pack.known, *pack.unknown])
        return pack_summary(pack) | {
            "peliculas": [movie_card(m) | {"conocida": m.netflix_id in known} for m in movies]
        }

    @app.get("/", dependencies=local)
    async def index() -> FileResponse:
        return FileResponse(PACKS_PAGE, headers={"Cache-Control": "no-cache"})

    @app.get("/api/packs", dependencies=local)
    async def list_packs() -> dict[str, Any]:
        return {"packs": [pack_summary(p) for p in store.list()]}

    @app.post("/api/packs", dependencies=write)
    async def create_pack(body: NewPack) -> dict[str, Any]:
        return detail(store.create(body.nombre))

    @app.get("/api/packs/{slug}", dependencies=local)
    async def get_pack(slug: str) -> dict[str, Any]:
        return detail(store.get(slug))

    @app.put("/api/packs/{slug}", dependencies=write)
    async def save_pack(slug: str, body: PackBody) -> dict[str, Any]:
        store.get(slug)  # debe existir: los packs se crean con POST
        pack = build_pack(
            slug, body.nombre, body.conocidas, body.desconocidas, body.proporcionConocidas
        )
        store.save(pack)
        return pack_summary(pack)

    @app.delete("/api/packs/{slug}", dependencies=write)
    async def delete_pack(slug: str) -> dict[str, Any]:
        store.delete(slug)
        return {"ok": True}

    @app.get("/api/peliculas", dependencies=local)
    async def movies(
        q: Annotated[str, Query(max_length=100)] = "",
        pagina: Annotated[int, Query(ge=1, le=1000)] = 1,
        categoria: Annotated[int | None, Query(gt=0)] = None,
    ) -> dict[str, Any]:
        if q.strip():
            page = search_movies(db, q, page=pagina, page_size=PAGE_SIZE, genre_id=categoria)
            results, total = page.results, page.total
        else:
            results, total = db.popular(
                limit=PAGE_SIZE, offset=(pagina - 1) * PAGE_SIZE, genre_id=categoria
            )
        return {
            "resultados": [movie_card(m) for m in results],
            "total": total,
            "pagina": pagina,
            "porPagina": PAGE_SIZE,
        }

    @app.get("/api/categorias", dependencies=local)
    async def genres() -> dict[str, Any]:
        return {
            "categorias": [
                {"id": g.id, "nombre": g.name, "peliculas": n} for g, n in db.genre_counts()
            ]
        }

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def serve_packs(config: AppConfig, *, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """Arranca el editor de packs y bloquea hasta que se detiene (Ctrl+C).

    Args:
        config: Configuración de la app (para localizar el catálogo y ``data/packs``).
        port: Puerto local.
        open_browser: Si se abre la página en el navegador del PC.

    Raises:
        SeedError: Si el catálogo incluido está dañado.
        OSError: Si el puerto está ocupado.
    """
    install_seed(db_path(config))
    db = CatalogDB(db_path(config))
    try:
        store = PackStore(config.resolve(config.catalog.data_dir) / PACKS_DIRNAME)
        url = f"http://localhost:{port}/"
        print(f"Editor de packs: {url}  ·  Ctrl+C para salir.")
        print(f"Los packs se guardan en {store.directory}")
        if open_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        uvicorn.run(create_packs_app(db, store), host="127.0.0.1", port=port, log_level="warning")
    finally:
        db.close()
