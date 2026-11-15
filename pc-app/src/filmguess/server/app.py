"""Aplicación FastAPI: rutas, autenticación, filtro de red y WebSocket.

Aquí no hay lógica de negocio: las rutas validan la petición, llaman a
:class:`~filmguess.game.session.GameSession` y convierten la respuesta a JSON.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from filmguess.game.session import GameError, GameSession
from filmguess.pairing.network import (
    is_allowed_client,
    is_localhost,
    pairing_url,
    qr_svg,
    token_matches,
)
from filmguess.panel.service import PanelError, PanelService
from filmguess.player.base import PlayerError
from filmguess.server import schemas
from filmguess.server.events import EventHub
from filmguess.webfiles import MOBILE_DIR, PANEL_PAGE, TV_PAGE, WEB_DIR

log = logging.getLogger(__name__)

ERROR_STATUS: dict[str, int] = {
    "token_invalido": 401,
    "red_no_permitida": 403,
    "solo_local": 403,
    "no_encontrada": 404,
    "jugador_no_encontrado": 404,
    "ruta_no_encontrada": 404,
    "sin_ronda": 409,
    "nombre_repetido": 409,
    "demasiados_jugadores": 409,
    "sin_candidatas": 409,
    "categoria_no_coincide": 422,
    "sin_pistas": 409,
    "sin_fotogramas": 409,
    "clip_usado": 409,
    "sin_revelar": 409,
    "ronda_cerrada": 409,
    "peticion_invalida": 422,
    "nombre_invalido": 422,
    "fuera_de_duracion": 422,
    "no_disponible_es": 409,
    "perfil_requerido": 409,
    "sesion_caducada": 409,
    "navegador_cerrado": 503,
    "tiempo_agotado": 504,
    "netflix_error": 502,
    "enlace_invalido": 422,
    "tmdb_no_encontrada": 404,
    "tmdb_error": 502,
    "sin_clave_tmdb": 409,
    "importando": 409,
    "sin_importador": 503,
    "config_error": 500,
}
"""Estado HTTP de cada código de error; los no listados devuelven 500."""

REVIEW_PAGE = 20
"""Películas por página en la lista de emparejamientos para revisar."""


async def _show_idle_when_ready(session: GameSession, attempts: int = 20) -> None:
    for _ in range(attempts):
        await asyncio.sleep(0.5)
        try:
            await session.player.idle()
        except PlayerError as exc:
            log.debug("Pantalla de espera aún no disponible: %s", exc.message)
            continue
        return
    log.warning("No se pudo abrir la pantalla de espera en el navegador de la tele.")


class ApiError(Exception):
    """Error que se devuelve como ``{ok: false, error, mensaje}``."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código de error.
            message: Mensaje en español.
        """
        super().__init__(message)
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict[str, Any]:
    """Cuerpo JSON de un error.

    Args:
        code: Código de error.
        message: Mensaje en español.

    Returns:
        ``{ok: false, error, mensaje}``.
    """
    return {"ok": False, "error": code, "mensaje": message}


class LocalNetworkOnly:
    """Middleware ASGI que rechaza clientes que no sean de la red local o del propio PC."""

    def __init__(self, app: ASGIApp) -> None:
        """Envuelve la aplicación.

        Args:
            app: Aplicación ASGI.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Filtra la petición por la IP del cliente.

        Args:
            scope: Ámbito ASGI.
            receive: Canal de entrada.
            send: Canal de salida.
        """
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = client[0] if client else None
            if not is_allowed_client(host):
                log.warning("Rechazado cliente fuera de la red local: %s", host)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps(
                    error_body("red_no_permitida", "Solo se aceptan dispositivos de tu red local.")
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class FreshStaticFiles(StaticFiles):
    """Ficheros estáticos que el navegador debe revalidar siempre.

    Sin esta cabecera, el móvil sigue usando su copia guardada tras actualizar la app.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def files_version(directory: Path) -> str:
    """Huella de una carpeta de ficheros web: cambia cuando cambia cualquiera de ellos.

    Args:
        directory: Carpeta servida.

    Returns:
        La fecha de modificación más reciente, como texto.
    """
    return str(max((f.stat().st_mtime_ns for f in directory.rglob("*") if f.is_file()), default=0))


def create_app(
    session: GameSession,
    token: str,
    *,
    port: int,
    ip: str,
    panel: PanelService | None = None,
) -> FastAPI:
    """Crea la aplicación del servidor.

    Args:
        session: Partida (catálogo, reproductor y marcador).
        token: Token de emparejamiento que deben enviar el móvil y el WebSocket.
        port: Puerto en el que escucha (para la URL del QR).
        ip: IP del PC en la red local (para la URL del QR).
        panel: Servicio del panel (token, importación, correcciones). Si no se da, se crea
            uno sin TMDB ni importador.

    Returns:
        La aplicación FastAPI.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await session.player.start()
        except PlayerError as exc:
            # El servidor sigue: el móvil verá el error y se reintenta en la siguiente orden.
            session.last_error = {"error": exc.code, "mensaje": exc.message}
            log.error("No se pudo preparar el reproductor: %s", exc.message)
        # La pantalla de espera es una página de este servidor: se abre cuando ya escucha.
        idle_task = asyncio.create_task(_show_idle_when_ready(session))
        try:
            yield
        finally:
            idle_task.cancel()
            await session.player.close()

    app = FastAPI(
        title="Adivina la Peli", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    hub = EventHub()
    panel = panel or PanelService(session.db, token=token)
    app.state.hub = hub
    app.state.session = session
    app.state.panel = panel

    async def publish(kind: str, data: dict[str, Any]) -> None:
        if kind == "estado":
            data = schemas.status(await session.state())
        elif kind == "marcador":
            data = schemas.scoreboard(session.scoreboard.players)
        await hub.broadcast(kind, data)

    session.on_event = publish

    # --- Errores -----------------------------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), ERROR_STATUS.get(exc.code, 500))

    @app.exception_handler(PanelError)
    async def panel_error(_: Request, exc: PanelError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), ERROR_STATUS.get(exc.code, 500))

    @app.exception_handler(GameError)
    async def game_error(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), ERROR_STATUS.get(exc.code, 500))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
        message = "Faltan datos o no son válidos" + (f": {fields}." if fields else ".")
        return JSONResponse(error_body("peticion_invalida", message), 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(error_body("ruta_no_encontrada", "Esa dirección no existe."), 404)
        return JSONResponse(error_body("error_http", str(exc.detail)), exc.status_code)

    # --- Dependencias ----------------------------------------------------------------

    async def require_token(x_token: Annotated[str | None, Header()] = None) -> None:
        if not token_matches(x_token, panel.token):
            raise ApiError(
                "token_invalido", "El móvil no está emparejado. Escanea de nuevo el QR del PC."
            )

    async def require_localhost(request: Request) -> None:
        if not is_localhost(request.client.host if request.client else None):
            raise ApiError("solo_local", "Esta página solo se puede abrir en el propio PC.")

    authed = [Depends(require_token)]

    # --- Rutas -----------------------------------------------------------------------

    @app.get("/status", dependencies=authed)
    async def get_status() -> dict[str, Any]:
        return schemas.status(await session.state())

    @app.get("/search", dependencies=authed)
    async def get_search(
        q: Annotated[str, Query(max_length=100)] = "",
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        todas: bool = False,
        categoria: Annotated[int | None, Query(gt=0)] = None,
    ) -> dict[str, Any]:
        return schemas.search_page(
            session.search(q, page=page, include_all=todas, genre_id=categoria)
        )

    @app.get("/random", dependencies=authed)
    async def get_random(categoria: Annotated[int | None, Query(gt=0)] = None) -> dict[str, Any]:
        return schemas.movie_detail(session.random_movie(categoria))

    @app.get("/genres", dependencies=authed)
    async def get_genres() -> dict[str, Any]:
        return schemas.genres(session.genres())

    @app.get("/movie/{netflix_id}", dependencies=authed)
    async def get_movie(netflix_id: int) -> dict[str, Any]:
        return schemas.movie_detail(session.movie(netflix_id))

    @app.post("/play", dependencies=authed)
    async def post_play(body: schemas.PlayRequest) -> dict[str, Any]:
        actual = await session.play(body.netflixId, body.seconds, body.categoria)
        return {"ok": True, "actualSeconds": actual}

    @app.post("/hint", dependencies=authed)
    async def post_hint() -> dict[str, Any]:
        hint = await session.reveal_hint()
        return {"ok": True, "etiqueta": hint.label, "texto": hint.text}

    @app.post("/frame", dependencies=authed)
    async def post_frame() -> dict[str, Any]:
        return {"ok": True, "actualSeconds": await session.next_frame()}

    @app.post("/skip", dependencies=authed)
    async def post_skip() -> dict[str, Any]:
        return {"ok": True, "actualSeconds": await session.skip_scene()}

    @app.post("/clip", dependencies=authed)
    async def post_clip() -> dict[str, Any]:
        await session.clip()
        return {"ok": True}

    @app.post("/award", dependencies=authed)
    async def post_award(body: schemas.AwardRequest) -> dict[str, Any]:
        await session.award(body.jugadores)
        return schemas.scoreboard(session.scoreboard.players)

    @app.get("/peek", dependencies=authed)
    async def get_peek() -> dict[str, Any]:
        return schemas.movie_summary(session.peek())

    @app.post("/fail", dependencies=authed)
    async def post_fail(body: schemas.AwardRequest) -> dict[str, Any]:
        await session.fail(body.jugadores)
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/pause", dependencies=authed)
    async def post_pause() -> dict[str, Any]:
        await session.pause()
        return {"ok": True}

    @app.post("/resume", dependencies=authed)
    async def post_resume() -> dict[str, Any]:
        await session.resume()
        return {"ok": True}

    @app.post("/reveal", dependencies=authed)
    async def post_reveal() -> dict[str, Any]:
        movie = await session.reveal()
        return {"ok": True, "titulo": movie.display_title} | schemas.movie_summary(movie)

    @app.post("/idle", dependencies=authed)
    async def post_idle() -> dict[str, Any]:
        await session.idle()
        return {"ok": True}

    @app.get("/players", dependencies=authed)
    async def get_players() -> dict[str, Any]:
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players", dependencies=authed, status_code=201)
    async def post_player(body: schemas.NewPlayerRequest) -> dict[str, Any]:
        await session.add_player(body.nombre)
        return schemas.scoreboard(session.scoreboard.players)

    @app.patch("/players/{player_id}", dependencies=authed)
    async def patch_player(player_id: str, body: schemas.NewPlayerRequest) -> dict[str, Any]:
        await session.rename_player(player_id, body.nombre)
        return schemas.scoreboard(session.scoreboard.players)

    @app.delete("/players/{player_id}", dependencies=authed)
    async def delete_player(player_id: str) -> dict[str, Any]:
        await session.remove_player(player_id)
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players/{player_id}/points", dependencies=authed)
    async def post_points(player_id: str, body: schemas.PointsRequest) -> dict[str, Any]:
        await session.add_points(player_id, body.puntos)
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players/reset", dependencies=authed)
    async def post_reset() -> dict[str, Any]:
        await session.reset_points()
        return schemas.scoreboard(session.scoreboard.players)

    @app.get("/tv", dependencies=[Depends(require_localhost)])
    async def get_tv() -> FileResponse:
        return FileResponse(TV_PAGE, media_type="text/html; charset=utf-8")

    @app.get("/pairing", dependencies=[Depends(require_localhost)])
    async def get_pairing() -> dict[str, Any]:
        return {"url": pairing_url(ip, port, panel.token), "ip": ip, "puerto": port}

    @app.get("/pairing/qr.svg", dependencies=[Depends(require_localhost)])
    async def get_pairing_qr() -> Response:
        return Response(
            qr_svg(pairing_url(ip, port, panel.token)),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/ws")
    async def websocket(
        websocket: WebSocket,
        token_param: Annotated[str | None, Query(alias="token")] = None,
    ) -> None:
        if not token_matches(token_param, panel.token):
            await websocket.close(code=1008)
            return
        await listen(websocket)

    @app.websocket("/tv/ws")
    async def tv_websocket(websocket: WebSocket) -> None:
        # La tele es una pestaña del propio PC: no lleva token, pero solo se acepta en local.
        if not is_localhost(websocket.client.host if websocket.client else None):
            await websocket.close(code=1008)
            return
        await listen(websocket, "tele")

    async def listen(websocket: WebSocket, role: str = "movil") -> None:
        await hub.connect(websocket, role)
        try:
            await hub.send(websocket, "estado", schemas.status(await session.state()))
            await hub.send(websocket, "marcador", schemas.scoreboard(session.scoreboard.players))
            while True:
                await websocket.receive_text()  # el cliente no envía órdenes por aquí
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    # --- Panel del PC (solo localhost) ----------------------------------------------------

    local = [Depends(require_localhost)]

    @app.get("/panel", dependencies=local)
    async def get_panel() -> FileResponse:
        return FileResponse(PANEL_PAGE, media_type="text/html; charset=utf-8")

    @app.get("/panel/api/estado", dependencies=local)
    async def panel_status() -> dict[str, Any]:
        state = schemas.status(await session.state())
        return state | {
            "moviles": hub.count_role("movil"),
            "tele": hub.count_role("tele"),
            "catalogo": panel.stats(),
            "importacion": dataclasses.asdict(panel.import_state),
            "conexion": {"url": pairing_url(ip, port, panel.token), "ip": ip, "puerto": port},
        }

    @app.post("/panel/api/token", dependencies=local)
    async def panel_new_token() -> dict[str, Any]:
        panel.regenerate_token()
        closed = await hub.close_role("movil")
        log.info("Código de conexión regenerado; %d móviles desconectados", closed)
        return {"ok": True, "url": pairing_url(ip, port, panel.token), "desconectados": closed}

    @app.post("/panel/api/importar", dependencies=local)
    async def panel_import() -> dict[str, Any]:
        state = panel.start_import()
        return {"ok": True, "importacion": dataclasses.asdict(state)}

    @app.get("/panel/api/revisar", dependencies=local)
    async def panel_review(
        confianza: Literal["baja", "sin_match"] = "baja",
        pagina: Annotated[int, Query(ge=1, le=10_000)] = 1,
    ) -> dict[str, Any]:
        movies, total = panel.to_review(confianza, pagina, REVIEW_PAGE)
        return {
            "resultados": [schemas.review_item(m) for m in movies],
            "total": total,
            "pagina": pagina,
            "porPagina": REVIEW_PAGE,
        }

    @app.post("/panel/api/peliculas/{netflix_id}/tmdb", dependencies=local)
    async def panel_correct(netflix_id: int, body: schemas.CorrectRequest) -> dict[str, Any]:
        movie = await panel.correct(netflix_id, body.tmdb)
        return {"ok": True, "pelicula": schemas.review_item(movie)}

    @app.post("/panel/api/peliculas", dependencies=local)
    async def panel_add(body: schemas.AddMovieRequest) -> dict[str, Any]:
        movie, added = await panel.add_by_link(body.netflix, body.tmdb)
        return {"ok": True, "nueva": added, "pelicula": schemas.review_item(movie)}

    @app.post("/panel/api/netflix-login", dependencies=local)
    async def panel_login() -> dict[str, Any]:
        await session.open_login()
        return {"ok": True}

    @app.post("/panel/api/espera", dependencies=local)
    async def panel_idle() -> dict[str, Any]:
        await session.idle()
        return {"ok": True}

    @app.get("/m", include_in_schema=False)
    async def mobile_redirect() -> RedirectResponse:
        return RedirectResponse("/m/")

    @app.get("/version", include_in_schema=False)
    async def get_version() -> dict[str, str]:
        return {"version": files_version(MOBILE_DIR)}

    app.mount("/static", FreshStaticFiles(directory=WEB_DIR), name="static")
    app.mount("/m", FreshStaticFiles(directory=MOBILE_DIR, html=True), name="mobile")
    app.add_middleware(LocalNetworkOnly)
    return app
