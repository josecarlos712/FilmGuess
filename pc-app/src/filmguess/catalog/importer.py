"""Importación del catálogo: CSV → SQLite → enriquecimiento con TMDB."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from filmguess.catalog.csv_source import read_movies_csv
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.matching import choose_match
from filmguess.catalog.tmdb import TmdbAuthError, TmdbClient, TmdbError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ImportProgress:
    """Progreso de una importación (se notifica tras cada película)."""

    total: int
    done: int = 0
    matched: int = 0
    no_match: int = 0
    errors: int = 0
    last_error: str | None = None


ProgressCallback = Callable[[ImportProgress], None]


def ingest_csv(db: CatalogDB, csv_path: Path) -> int:
    """Carga en la base de datos las películas del CSV que falten.

    Args:
        db: Catálogo.
        csv_path: Ruta del CSV.

    Returns:
        Número de películas nuevas.

    Raises:
        CsvSourceError: Si el CSV no se puede leer.
    """
    result = read_movies_csv(csv_path)
    added = db.add_csv_movies(result.movies)
    log.info(
        "CSV: %d válidas, %d nuevas, %d descartadas, %d repetidas",
        len(result.movies),
        added,
        result.skipped_invalid,
        result.skipped_duplicate,
    )
    return added


async def enrich_one(db: CatalogDB, client: TmdbClient, netflix_id: int, csv_title: str) -> bool:
    """Empareja una película con TMDB y guarda el resultado.

    Args:
        db: Catálogo.
        client: Cliente de TMDB.
        netflix_id: ID de Netflix.
        csv_title: Título del CSV.

    Returns:
        ``True`` si se encontró candidato; ``False`` si se marcó sin coincidencia.

    Raises:
        TmdbError: Si TMDB falla; la película queda pendiente para otro intento.
    """
    candidates = await client.search_movies(csv_title)
    match = await choose_match(csv_title, candidates, client.movie_details, client.region)
    if match is None:
        db.mark_no_match(netflix_id)
        return False
    db.save_match(netflix_id, match)
    return True


async def enrich_pending(
    db: CatalogDB,
    client: TmdbClient,
    *,
    limit: int | None = None,
    workers: int = 8,
    on_progress: ProgressCallback | None = None,
    max_consecutive_errors: int = 20,
) -> ImportProgress:
    """Enriquece con TMDB todas las películas pendientes.

    Se puede interrumpir y reanudar: cada película se guarda al terminarla y las que
    fallan siguen pendientes.

    Args:
        db: Catálogo.
        client: Cliente de TMDB.
        limit: Máximo de películas a procesar en esta ejecución.
        workers: Peticiones simultáneas (el ritmo lo limita el cliente).
        on_progress: Se llama tras cada película.
        max_consecutive_errors: Aborta si TMDB falla tantas veces seguidas.

    Returns:
        El progreso final.

    Raises:
        TmdbError: Si la clave no es válida o hay demasiados errores seguidos.
    """
    pending = db.pending(limit)
    progress = ImportProgress(total=len(pending))
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for item in pending:
        queue.put_nowait(item)
    consecutive_errors = 0
    fatal: list[TmdbError] = []

    async def worker() -> None:
        nonlocal consecutive_errors
        while not fatal:
            try:
                netflix_id, title = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if await enrich_one(db, client, netflix_id, title):
                    progress.matched += 1
                else:
                    progress.no_match += 1
                consecutive_errors = 0
            except TmdbError as exc:
                progress.errors += 1
                progress.last_error = str(exc)
                consecutive_errors += 1
                log.warning("TMDB falló con «%s» (%d): %s", title, netflix_id, exc)
                if isinstance(exc, TmdbAuthError) or consecutive_errors >= max_consecutive_errors:
                    fatal.append(exc)
            progress.done += 1
            if on_progress:
                on_progress(progress)

    await asyncio.gather(*(worker() for _ in range(max(1, workers))))
    if fatal:
        raise TmdbError(f"Importación detenida: {fatal[0]}")
    return progress
