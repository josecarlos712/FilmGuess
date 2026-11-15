"""Orquestación de la importación completa del catálogo (usada por la CLI y el panel)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.imdb import apply_ratings, download_ratings
from filmguess.catalog.importer import ImportProgress, enrich_pending, ingest_csv
from filmguess.catalog.tmdb import TmdbClient
from filmguess.config import AppConfig

DB_FILENAME = "catalogo.sqlite"
IMDB_FILENAME = "title.ratings.tsv.gz"


def db_path(config: AppConfig) -> Path:
    """Ruta del fichero SQLite del catálogo.

    Args:
        config: Configuración de la app.

    Returns:
        Ruta absoluta.
    """
    return config.resolve(config.catalog.data_dir) / DB_FILENAME


def imdb_path(config: AppConfig) -> Path:
    """Ruta local del fichero de valoraciones de IMDb.

    Args:
        config: Configuración de la app.

    Returns:
        Ruta absoluta.
    """
    return config.resolve(config.catalog.data_dir) / IMDB_FILENAME


@dataclass(slots=True)
class ImportOptions:
    """Qué pasos ejecutar en una importación."""

    csv: bool = True
    tmdb: bool = True
    imdb: bool = True
    download_imdb: bool = False
    limit: int | None = None


@dataclass(slots=True)
class ImportSummary:
    """Resultado de la importación."""

    csv_added: int = 0
    tmdb: ImportProgress | None = None
    imdb_updated: int | None = None
    imdb_missing: bool = False


def run_import(
    config: AppConfig,
    options: ImportOptions,
    on_progress: Callable[[ImportProgress], None] | None = None,
) -> ImportSummary:
    """Ejecuta los pasos de importación pedidos.

    Args:
        config: Configuración de la app.
        options: Pasos a ejecutar.
        on_progress: Progreso del enriquecimiento con TMDB.

    Returns:
        Resumen de lo hecho.

    Raises:
        CsvSourceError: Si el CSV no se puede leer.
        TmdbError: Si TMDB rechaza la clave o falla de forma continuada.
        ImdbError: Si la descarga o lectura de IMDb falla.
    """
    summary = ImportSummary()
    db = CatalogDB(db_path(config))
    try:
        if options.csv:
            summary.csv_added = ingest_csv(db, config.resolve(config.catalog.csv_file))
        if options.tmdb:
            summary.tmdb = asyncio.run(_enrich(db, config, options.limit, on_progress))
        if options.imdb:
            ratings = imdb_path(config)
            if options.download_imdb:
                download_ratings(ratings)
            if ratings.exists():
                summary.imdb_updated = apply_ratings(db, ratings)
            else:
                summary.imdb_missing = True
    finally:
        db.close()
    return summary


async def _enrich(
    db: CatalogDB,
    config: AppConfig,
    limit: int | None,
    on_progress: Callable[[ImportProgress], None] | None,
) -> ImportProgress:
    async with TmdbClient(
        config.tmdb.api_key, language=config.tmdb.language, region=config.tmdb.region
    ) as client:
        return await enrich_pending(db, client, limit=limit, on_progress=on_progress)
