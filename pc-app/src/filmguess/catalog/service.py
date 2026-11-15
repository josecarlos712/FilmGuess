"""Orquestación de la importación completa del catálogo (usada por la CLI y el panel)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.imdb import apply_ratings, download_ratings
from filmguess.catalog.importer import ImportProgress, enrich_pending, ingest_csv, ingest_wikidata
from filmguess.catalog.platforms import (
    ALL_KINDS,
    SUBSCRIPTION_KINDS,
    HarvestProgress,
    HarvestSummary,
    PlatformsDB,
    apply_imdb_ratings,
    export_csv,
    fill_imdb_ids,
    harvest,
)
from filmguess.catalog.platforms import ProgressCallback as HarvestCallback
from filmguess.catalog.tmdb import TmdbClient
from filmguess.catalog.wikidata import download_wikidata
from filmguess.config import AppConfig

DB_FILENAME = "catalogo.sqlite"
IMDB_FILENAME = "title.ratings.tsv.gz"
WIKIDATA_FILENAME = "wikidata_netflix.csv"
PLATFORMS_FILENAME = "plataformas.sqlite"


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


def wikidata_path(config: AppConfig) -> Path:
    """Ruta local del CSV de IDs de Netflix descargado de Wikidata.

    Args:
        config: Configuración de la app.

    Returns:
        Ruta absoluta.
    """
    return config.resolve(config.catalog.data_dir) / WIKIDATA_FILENAME


@dataclass(slots=True)
class ImportOptions:
    """Qué pasos ejecutar en una importación."""

    csv: bool = True
    wikidata: bool = True
    download_wikidata: bool = False
    tmdb: bool = True
    imdb: bool = True
    download_imdb: bool = False
    limit: int | None = None


@dataclass(slots=True)
class ImportSummary:
    """Resultado de la importación."""

    csv_added: int = 0
    wikidata_added: int | None = None
    wikidata_reopened: int = 0
    wikidata_missing: bool = False
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
        WikidataError: Si la descarga o lectura de Wikidata falla.
        TmdbError: Si TMDB rechaza la clave o falla de forma continuada.
        ImdbError: Si la descarga o lectura de IMDb falla.
    """
    summary = ImportSummary()
    db = CatalogDB(db_path(config))
    try:
        if options.csv:
            summary.csv_added = ingest_csv(db, config.resolve(config.catalog.csv_file))
        if options.wikidata:
            wd_file = wikidata_path(config)
            if options.download_wikidata:
                download_wikidata(wd_file)
            if wd_file.exists():
                summary.wikidata_added, summary.wikidata_reopened = ingest_wikidata(db, wd_file)
            else:
                summary.wikidata_missing = True
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


def platforms_db_path(config: AppConfig) -> Path:
    """Ruta del catálogo multiplataforma (separado del catálogo del juego).

    Args:
        config: Configuración de la app.

    Returns:
        Ruta absoluta.
    """
    return config.resolve(config.catalog.data_dir) / PLATFORMS_FILENAME


def run_harvest(
    config: AppConfig,
    *,
    wikidata: bool = True,
    check: bool = True,
    check_limit: int | None = None,
    recheck: bool = False,
    on_progress: HarvestCallback | None = None,
) -> tuple[HarvestSummary, list[tuple[str, str, int]]]:
    """Descarga el catálogo de todas las plataformas de la región configurada.

    Args:
        config: Configuración de la app.
        wikidata: Si se descargan los IDs propios de cada plataforma.
        check: Si se comprueba la disponibilidad exacta de cada película.
        check_limit: Máximo de películas a comprobar en esta ejecución.
        recheck: Si se vuelven a comprobar también las ya comprobadas.
        on_progress: Progreso de cada fase.

    Returns:
        El resumen y las películas por plataforma y tipo de oferta.

    Raises:
        TmdbError: Si TMDB rechaza la clave o falla de forma continuada.
    """
    db = PlatformsDB(platforms_db_path(config))
    try:
        if recheck:
            db.reset_checks()
        summary = asyncio.run(_harvest(db, config, wikidata, check, check_limit, on_progress))
        return summary, db.stats()
    finally:
        db.close()


async def _harvest(
    db: PlatformsDB,
    config: AppConfig,
    wikidata: bool,
    check: bool,
    check_limit: int | None,
    on_progress: HarvestCallback | None,
) -> HarvestSummary:
    async with TmdbClient(
        config.tmdb.api_key, language=config.tmdb.language, region=config.tmdb.region
    ) as client:
        return await harvest(
            db,
            client,
            cache_dir=config.resolve(config.catalog.data_dir),
            wikidata=wikidata,
            check=check,
            check_limit=check_limit,
            on_progress=on_progress,
        )


@dataclass(slots=True)
class PlatformExport:
    """Resultado de :func:`run_platform_export`."""

    rows: int = 0
    imdb_lookups: int = 0
    imdb_rated: int | None = None
    imdb_missing: bool = False


def run_platform_export(
    config: AppConfig,
    dest: Path,
    *,
    lookup_ids: bool = True,
    download_imdb: bool = False,
    subscription_only: bool = False,
    on_progress: HarvestCallback | None = None,
) -> PlatformExport:
    """Añade valoraciones de IMDb al catálogo multiplataforma y lo exporta a CSV.

    Args:
        config: Configuración de la app.
        dest: Ruta del CSV.
        lookup_ids: Si se piden a TMDB los IMDb ID que falten.
        download_imdb: Si se descarga antes el fichero de valoraciones de IMDb.
        subscription_only: Solo suscripción, gratis y con anuncios (sin alquiler ni compra).
        on_progress: Progreso de la consulta de IDs.

    Returns:
        Resumen de lo hecho.

    Raises:
        TmdbError: Si TMDB rechaza la clave o falla de forma continuada.
        ImdbError: Si la descarga o lectura de IMDb falla.
        OSError: Si no se puede escribir el CSV.
    """
    result = PlatformExport()
    db = PlatformsDB(platforms_db_path(config))
    try:
        if lookup_ids:
            result.imdb_lookups = asyncio.run(_fill_ids(db, config, on_progress)).done
        ratings = imdb_path(config)
        if download_imdb:
            download_ratings(ratings)
        if ratings.exists():
            result.imdb_rated = apply_imdb_ratings(db, ratings)
        else:
            result.imdb_missing = True
        kinds = SUBSCRIPTION_KINDS if subscription_only else ALL_KINDS
        result.rows = export_csv(db, dest, kinds=kinds)
    finally:
        db.close()
    return result


async def _fill_ids(
    db: PlatformsDB, config: AppConfig, on_progress: HarvestCallback | None
) -> HarvestProgress:
    async with TmdbClient(
        config.tmdb.api_key, language=config.tmdb.language, region=config.tmdb.region
    ) as client:
        return await fill_imdb_ids(db, client, on_progress=on_progress)
