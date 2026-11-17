"""Descarga del idioma original y los países de origen de cada película (TMDB)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from filmguess.catalog.db import CatalogDB, ClueTarget
from filmguess.catalog.tmdb import TmdbAuthError, TmdbClient, TmdbError, TmdbNotFoundError

log = logging.getLogger(__name__)

CONCURRENCY = 8


@dataclass(slots=True)
class OriginProgress:
    """Progreso de la descarga de orígenes."""

    total: int = 0
    done: int = 0
    errors: int = 0


ProgressCallback = Callable[[OriginProgress], None]


def parse_origin(data: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Extrae el idioma original y los países de origen de la ficha de TMDB.

    Args:
        data: JSON de ``/movie/{id}``.

    Returns:
        ``(idioma, países)``; si ``origin_country`` está vacío se usan
        ``production_countries``.
    """
    language = data.get("original_language") or None
    countries = [str(c).upper() for c in data.get("origin_country") or [] if c]
    if not countries:
        countries = [
            str(c["iso_3166_1"]).upper()
            for c in data.get("production_countries") or []
            if c.get("iso_3166_1")
        ]
    return (str(language).lower() if language else None), countries


async def fetch_origin(
    db: CatalogDB,
    tmdb: TmdbClient,
    *,
    refresh: bool = False,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> OriginProgress:
    """Descarga y guarda el origen de las películas del catálogo de juego.

    Args:
        db: Catálogo.
        tmdb: Cliente de TMDB.
        refresh: Vuelve a descargar también las que ya tienen origen.
        limit: Máximo de películas.
        on_progress: Recibe el progreso.

    Returns:
        El resumen.

    Raises:
        TmdbAuthError: Si TMDB rechaza la clave.
    """
    targets = db.origin_targets(refresh=refresh)[:limit]
    progress = OriginProgress(total=len(targets))
    report = on_progress or (lambda _p: None)
    report(progress)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(target: ClueTarget) -> None:
        async with semaphore:
            try:
                language, countries = parse_origin(await tmdb.movie_origin(target.tmdb_id))
                db.save_origin(target.netflix_id, language, countries)
            except TmdbAuthError:
                raise
            except TmdbNotFoundError:
                db.save_origin(target.netflix_id, None, [])
            except TmdbError as exc:
                progress.errors += 1
                log.warning("TMDB falló en %s: %s", target.netflix_id, exc)
            progress.done += 1
            report(progress)

    await asyncio.gather(*(one(t) for t in targets))
    return progress
