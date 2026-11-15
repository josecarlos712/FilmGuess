"""Interfaz de línea de comandos de la app de PC."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from filmguess.catalog.csv_source import CsvSourceError
from filmguess.catalog.imdb import ImdbError
from filmguess.catalog.importer import ImportProgress
from filmguess.catalog.service import ImportOptions, run_import
from filmguess.catalog.tmdb import TmdbError
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
    imp.add_argument("--sin-tmdb", action="store_true", help="solo carga el CSV, sin TMDB")
    imp.add_argument("--sin-imdb", action="store_true", help="no aplica valoraciones de IMDb")
    imp.add_argument(
        "--descargar-imdb",
        action="store_true",
        help="descarga antes title.ratings.tsv.gz de IMDb (unos 8 MB)",
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

    print(f"El comando «{command}» todavía no está implementado.")
    return 1


def _cmd_import(config: AppConfig, args: argparse.Namespace) -> int:
    options = ImportOptions(
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
    except (CsvSourceError, TmdbError, ImdbError) as exc:
        print(f"Error en la importación: {exc}")
        return 1

    print(f"CSV: {summary.csv_added} películas nuevas.")
    if summary.tmdb is not None:
        t = summary.tmdb
        print(f"TMDB: {t.matched} emparejadas, {t.no_match} sin coincidencia, {t.errors} errores.")
    if summary.imdb_updated is not None:
        print(f"IMDb: {summary.imdb_updated} valoraciones aplicadas.")
    elif summary.imdb_missing:
        print("IMDb: no hay fichero de valoraciones; usa --descargar-imdb para obtenerlo.")
    return 0
