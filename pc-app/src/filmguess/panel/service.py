"""Lógica del panel del PC: token, importación, corrección de emparejamientos y altas.

Las rutas de ``/panel/api`` solo llaman a :class:`PanelService`; aquí no hay nada de HTTP.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.importer import ImportProgress
from filmguess.catalog.matching import build_match
from filmguess.catalog.models import Confidence, Movie
from filmguess.catalog.tmdb import TmdbError, TmdbNotFoundError
from filmguess.config import generate_token

log = logging.getLogger(__name__)

_NETFLIX_ID = re.compile(
    r"netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:title|watch)/(\d{5,10})", re.I
)
_TMDB_ID = re.compile(r"themoviedb\.org/(?:[a-z]{2}(?:-[a-z]{2})?/)?movie/(\d{1,9})", re.I)
_PLAIN_ID = re.compile(r"\d{1,10}")


def _find_id(pattern: re.Pattern[str], text: str) -> int | None:
    clean = text.strip()
    if _PLAIN_ID.fullmatch(clean):
        return int(clean)
    match = pattern.search(clean)
    return int(match.group(1)) if match else None


class PanelError(Exception):
    """Error del panel con código y mensaje en español."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código (``enlace_invalido``, ``tmdb_error``, ``importando``...).
            message: Mensaje para la persona que usa el panel.
        """
        super().__init__(message)
        self.code = code
        self.message = message


class DetailsClient(Protocol):
    """Lo que el panel usa del cliente de TMDB."""

    region: str

    async def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        """Ficha de TMDB."""
        ...


TmdbFactory = Callable[[], AbstractAsyncContextManager[DetailsClient]]
ImportRunner = Callable[[Callable[[ImportProgress], None]], Any]


def parse_netflix_id(text: str) -> int:
    """ID de Netflix de un enlace (``/title/ID`` o ``/watch/ID``) o de un número.

    Args:
        text: Enlace o número.

    Returns:
        El ID.

    Raises:
        PanelError: Con ``enlace_invalido`` si no hay un ID reconocible.
    """
    found = _find_id(_NETFLIX_ID, text)
    if not found:
        raise PanelError(
            "enlace_invalido",
            "No reconozco ese enlace de Netflix. Copia el de la película (…/title/12345678).",
        )
    return found


def parse_tmdb_id(text: str) -> int:
    """ID de TMDB de un enlace (``themoviedb.org/movie/ID-nombre``) o de un número.

    Args:
        text: Enlace o número.

    Returns:
        El ID.

    Raises:
        PanelError: Con ``enlace_invalido`` si no hay un ID reconocible.
    """
    found = _find_id(_TMDB_ID, text)
    if not found:
        raise PanelError(
            "enlace_invalido",
            "No reconozco ese enlace de TMDB. Copia el de la ficha (…/movie/27205-inception).",
        )
    return found


@dataclass(slots=True)
class ImportState:
    """Cómo va la importación lanzada desde el panel."""

    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    total: int = 0
    done: int = 0
    matched: int = 0
    no_match: int = 0
    errors: int = 0
    message: str | None = None
    failed: bool = False
    summary: dict[str, Any] = field(default_factory=dict)


class PanelService:
    """Operaciones del panel."""

    def __init__(
        self,
        db: CatalogDB,
        *,
        token: str,
        save_token: Callable[[str], None] | None = None,
        tmdb: TmdbFactory | None = None,
        run_import: ImportRunner | None = None,
    ) -> None:
        """Crea el servicio.

        Args:
            db: Catálogo (la misma conexión que usa la partida).
            token: Token de emparejamiento actual.
            save_token: Guarda un token nuevo en ``config.toml``.
            tmdb: Abre un cliente de TMDB (``async with``); ``None`` si no hay clave.
            run_import: Ejecuta la importación completa (bloqueante) con un aviso de progreso.
        """
        self.db = db
        self.token = token
        self._save_token = save_token
        self._tmdb = tmdb
        self._run_import = run_import
        self.import_state = ImportState()
        self._import_task: asyncio.Task[None] | None = None

    @property
    def has_tmdb(self) -> bool:
        """Si hay clave de TMDB para importar, corregir y añadir películas."""
        return self._tmdb is not None

    # --- Token ---------------------------------------------------------------------

    def regenerate_token(self) -> str:
        """Crea un token nuevo, lo guarda y lo devuelve (los móviles tendrán que volver a escanear).

        Returns:
            El token nuevo.

        Raises:
            PanelError: Con ``config_error`` si no se puede guardar.
        """
        token = generate_token()
        if self._save_token is not None:
            try:
                self._save_token(token)
            except OSError as exc:
                raise PanelError(
                    "config_error", f"No se pudo guardar el código nuevo en config.toml: {exc}"
                ) from exc
        self.token = token
        return token

    # --- Catálogo ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Recuentos del catálogo.

        Returns:
            Los de :meth:`CatalogDB.stats`.
        """
        return self.db.stats()

    def to_review(
        self, confidence: Confidence, page: int, page_size: int
    ) -> tuple[list[Movie], int]:
        """Emparejamientos dudosos.

        Args:
            confidence: ``baja`` o ``sin_match``.
            page: Página (desde 1).
            page_size: Tamaño de página.

        Returns:
            La página y el total.
        """
        return self.db.to_review(confidence, limit=page_size, offset=(page - 1) * page_size)

    async def correct(self, netflix_id: int, tmdb_text: str) -> Movie:
        """Corrige a mano el emparejamiento de una película.

        Args:
            netflix_id: Película del catálogo.
            tmdb_text: Enlace o ID de TMDB correcto.

        Returns:
            La película actualizada.

        Raises:
            PanelError: Si la película no existe, el enlace no vale o TMDB falla.
        """
        if self.db.get(netflix_id) is None:
            raise PanelError("no_encontrada", "Esa película ya no está en el catálogo.")
        tmdb_id = parse_tmdb_id(tmdb_text)
        details, region = await self._details(tmdb_id)
        self.db.save_match(netflix_id, build_match(details, "alta", region), manual=True)
        return self._get(netflix_id)

    async def add_by_link(self, netflix_text: str, tmdb_text: str) -> tuple[Movie, bool]:
        """Da de alta (o corrige) una película a partir de sus enlaces.

        Args:
            netflix_text: Enlace o ID de Netflix.
            tmdb_text: Enlace o ID de TMDB.

        Returns:
            La película y si es nueva en el catálogo.

        Raises:
            PanelError: Si algún enlace no vale o TMDB falla.
        """
        netflix_id = parse_netflix_id(netflix_text)
        tmdb_id = parse_tmdb_id(tmdb_text)
        details, region = await self._details(tmdb_id)
        match = build_match(details, "alta", region)
        # Quien la da de alta tiene el enlace de Netflix: está disponible.
        match = dataclasses.replace(match, netflix_es=True)
        title = match.title_es or match.original_title or f"Netflix {netflix_id}"
        added = self.db.add_csv_movies([CsvMovie(netflix_id, title)]) > 0
        self.db.save_match(netflix_id, match, manual=True)
        return self._get(netflix_id), added

    async def _details(self, tmdb_id: int) -> tuple[dict[str, Any], str]:
        if self._tmdb is None:
            raise PanelError(
                "sin_clave_tmdb", "Falta la clave de TMDB en config.toml ([tmdb] api_key)."
            )
        try:
            async with self._tmdb() as client:
                return await client.movie_details(tmdb_id), client.region
        except TmdbNotFoundError as exc:
            raise PanelError("tmdb_no_encontrada", f"TMDB no tiene la película {tmdb_id}.") from exc
        except TmdbError as exc:
            raise PanelError("tmdb_error", f"TMDB no responde: {exc}") from exc

    def _get(self, netflix_id: int) -> Movie:
        movie = self.db.get(netflix_id)
        if movie is None:  # pragma: no cover - se acaba de guardar
            raise PanelError("no_encontrada", "Esa película ya no está en el catálogo.")
        return movie

    # --- Importación -----------------------------------------------------------------

    def start_import(self) -> ImportState:
        """Lanza la importación en segundo plano (CSV, Wikidata, TMDB e IMDb).

        Returns:
            El estado recién iniciado.

        Raises:
            PanelError: Si ya hay una en marcha o no se puede importar.
        """
        if self._run_import is None:
            raise PanelError("sin_importador", "La importación no está disponible.")
        if self.import_state.running:
            raise PanelError("importando", "Ya hay una importación en marcha.")
        self.import_state = ImportState(running=True, started_at=_now())
        self._import_task = asyncio.create_task(self._import())
        return self.import_state

    async def _import(self) -> None:
        state = self.import_state
        runner = self._run_import
        assert runner is not None

        def progress(p: ImportProgress) -> None:
            state.total, state.done = p.total, p.done
            state.matched, state.no_match, state.errors = p.matched, p.no_match, p.errors
            state.message = p.last_error

        try:
            summary = await asyncio.to_thread(runner, progress)
        except Exception as exc:  # frontera de la tarea: se informa en el panel
            log.exception("La importación desde el panel ha fallado")
            state.failed = True
            state.message = f"La importación ha fallado: {exc}"
        else:
            if dataclasses.is_dataclass(summary) and not isinstance(summary, type):
                state.summary = _plain(dataclasses.asdict(summary))
        finally:
            state.running = False
            state.finished_at = _now()

    async def wait_import(self) -> None:
        """Espera a que termine la importación en curso (para pruebas y el cierre)."""
        if self._import_task is not None:
            await self._import_task


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if isinstance(v, int | bool | str | dict | None)}
