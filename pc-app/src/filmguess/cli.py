"""Interfaz de línea de comandos de la app de PC."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from filmguess.catalog.csv_source import CsvSourceError
from filmguess.catalog.imdb import ImdbError
from filmguess.catalog.importer import ImportProgress
from filmguess.catalog.platforms import HarvestProgress
from filmguess.catalog.service import (
    ImportOptions,
    run_harvest,
    run_import,
    run_platform_export,
)
from filmguess.catalog.tmdb import TmdbError
from filmguess.catalog.wikidata import WikidataError
from filmguess.config import DEFAULT_CONFIG_PATH, AppConfig, ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    """Crea el analizador de argumentos.

    Returns:
        El ``ArgumentParser`` con todas las opciones y subcomandos.
    """
    parser = argparse.ArgumentParser(
        prog="adivina-la-peli",
        description="Adivina la Peli: servidor del PC que controla Netflix desde el móvil.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="ruta del fichero de configuración (por defecto: pc-app/config.toml)",
    )
    sub = parser.add_subparsers(dest="command", metavar="comando")
    sub.add_parser("servir", help="arranca el servidor y el navegador (por defecto)")
    imp = sub.add_parser("importar", help="importa o actualiza el catálogo desde el CSV y TMDB")
    imp.add_argument("--limite", type=int, default=None, help="máximo de películas a enriquecer")
    imp.add_argument(
        "--sin-wikidata", action="store_true", help="no usa los IDs de Netflix de Wikidata"
    )
    imp.add_argument(
        "--descargar-wikidata",
        action="store_true",
        help="descarga antes de Wikidata los IDs de Netflix con su ID de TMDB (unos 2 MB)",
    )
    imp.add_argument("--sin-tmdb", action="store_true", help="solo carga el CSV, sin TMDB")
    imp.add_argument("--sin-imdb", action="store_true", help="no aplica valoraciones de IMDb")
    imp.add_argument(
        "--descargar-imdb",
        action="store_true",
        help="descarga antes title.ratings.tsv.gz de IMDb (unos 8 MB)",
    )
    plat = sub.add_parser(
        "plataformas",
        help="descarga aparte el catálogo de todas las plataformas del país (TMDB y Wikidata)",
    )
    plat.add_argument("--sin-wikidata", action="store_true", help="no descarga IDs de Wikidata")
    plat.add_argument(
        "--sin-comprobar",
        action="store_true",
        help="solo enumera; no comprueba la disponibilidad exacta de cada película",
    )
    plat.add_argument("--limite", type=int, default=None, help="máximo de películas a comprobar")
    plat.add_argument(
        "--recomprobar", action="store_true", help="vuelve a comprobar también las ya comprobadas"
    )
    exp = sub.add_parser(
        "exportar-plataformas",
        help="añade valoraciones de IMDb al catálogo multiplataforma y lo exporta a CSV",
    )
    exp.add_argument(
        "--salida",
        type=Path,
        default=None,
        help="ruta del CSV (por defecto data/peliculas_espana.csv)",
    )
    exp.add_argument(
        "--descargar-imdb",
        action="store_true",
        help="descarga antes title.ratings.tsv.gz de IMDb (unos 8 MB)",
    )
    exp.add_argument(
        "--sin-buscar-ids", action="store_true", help="no pide a TMDB los IMDb ID que falten"
    )
    exp.add_argument(
        "--solo-suscripcion",
        action="store_true",
        help="solo suscripción, gratis y con anuncios (sin alquiler ni compra)",
    )
    sub.add_parser("config", help="muestra la configuración efectiva")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta el comando pedido.

    Args:
        argv: Argumentos (sin el nombre del programa); ``None`` usa ``sys.argv``.

    Returns:
        Código de salida del proceso.
    """
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Error de configuración: {exc}")
        return 2

    command = args.command or "servir"
    if command == "config":
        shown = config.model_copy(deep=True)
        if shown.tmdb.api_key:
            shown.tmdb.api_key = "****"
        print(shown.model_dump_json(indent=2))
        return 0

    if command == "importar":
        return _cmd_import(config, args)
    if command == "plataformas":
        return _cmd_platforms(config, args)
    if command == "exportar-plataformas":
        return _cmd_export_platforms(config, args)

    print(f"El comando «{command}» todavía no está implementado.")
    return 1


def _cmd_import(config: AppConfig, args: argparse.Namespace) -> int:
    options = ImportOptions(
        wikidata=not args.sin_wikidata,
        download_wikidata=args.descargar_wikidata,
        tmdb=not args.sin_tmdb,
        imdb=not args.sin_imdb,
        download_imdb=args.descargar_imdb,
        limit=args.limite,
    )

    def progress(p: ImportProgress) -> None:
        if p.done % 50 == 0 or p.done == p.total:
            print(
                f"  TMDB {p.done}/{p.total} · emparejadas {p.matched} · "
                f"sin coincidencia {p.no_match} · errores {p.errors}",
                flush=True,
            )

    try:
        summary = run_import(config, options, progress)
    except (CsvSourceError, WikidataError, TmdbError, ImdbError) as exc:
        print(f"Error en la importación: {exc}")
        return 1

    print(f"CSV: {summary.csv_added} películas nuevas.")
    if summary.wikidata_added is not None:
        print(
            f"Wikidata: {summary.wikidata_added} películas nuevas, "
            f"{summary.wikidata_reopened} emparejamientos a corregir."
        )
    elif summary.wikidata_missing:
        print("Wikidata: no hay fichero de IDs; usa --descargar-wikidata para obtenerlo.")
    if summary.tmdb is not None:
        t = summary.tmdb
        print(f"TMDB: {t.matched} emparejadas, {t.no_match} sin coincidencia, {t.errors} errores.")
    if summary.imdb_updated is not None:
        print(f"IMDb: {summary.imdb_updated} valoraciones aplicadas.")
    elif summary.imdb_missing:
        print("IMDb: no hay fichero de valoraciones; usa --descargar-imdb para obtenerlo.")
    return 0


def _cmd_platforms(config: AppConfig, args: argparse.Namespace) -> int:
    def progress(p: HarvestProgress) -> None:
        if p.stage != "disponibilidad" or p.done % 500 == 0 or p.done == p.total:
            print(f"  {p.stage} {p.done}/{p.total} · errores {p.errors}", flush=True)

    try:
        summary, stats = run_harvest(
            config,
            wikidata=not args.sin_wikidata,
            check=not args.sin_comprobar,
            check_limit=args.limite,
            recheck=args.recomprobar,
            on_progress=progress,
        )
    except TmdbError as exc:
        print(f"Error en la descarga: {exc}")
        return 1

    print(
        f"Plataformas: {summary.providers} · resultados enumerados: {summary.discovered} · "
        f"comprobadas: {summary.checked} (errores {summary.check_errors})"
    )
    for platform, n in sorted(summary.external_ids.items()):
        print(f"  Wikidata {platform}: {n} IDs")
    for platform, error in sorted(summary.wikidata_errors.items()):
        print(f"  Wikidata {platform}: error: {error}")
    print("Películas por plataforma (tipo de oferta):")
    for name, kind, n in stats[:40]:
        print(f"  {n:6d}  {name} ({kind})")
    return 0


def _cmd_export_platforms(config: AppConfig, args: argparse.Namespace) -> int:
    def progress(p: HarvestProgress) -> None:
        if p.done % 1000 == 0 or p.done == p.total:
            print(f"  {p.stage} {p.done}/{p.total} · errores {p.errors}", flush=True)

    dest = args.salida or config.resolve(config.catalog.data_dir) / "peliculas_espana.csv"
    try:
        result = run_platform_export(
            config,
            dest,
            lookup_ids=not args.sin_buscar_ids,
            download_imdb=args.descargar_imdb,
            subscription_only=args.solo_suscripcion,
            on_progress=progress,
        )
    except (TmdbError, ImdbError, OSError) as exc:
        print(f"Error en la exportación: {exc}")
        return 1

    if result.imdb_rated is not None:
        print(f"IMDb: {result.imdb_rated} películas con valoración.")
    elif result.imdb_missing:
        print("IMDb: no hay fichero de valoraciones; usa --descargar-imdb para obtenerlo.")
    print(f"CSV: {result.rows} filas en {dest}")
    return 0
