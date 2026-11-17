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
    clues = sub.add_parser(
        "pistas",
        help="descarga las pistas del catálogo de juego (géneros, reparto, frase o crítica)",
    )
    clues.add_argument("--limite", type=int, default=None, help="máximo de películas")
    clues.add_argument(
        "--todas", action="store_true", help="vuelve a descargar también las que ya tienen pistas"
    )
    clues.add_argument(
        "--exportar-sin-traducir",
        type=Path,
        default=None,
        metavar="FICHERO",
        help="solo escribe en un JSON las frases y críticas que faltan por traducir",
    )
    clues.add_argument(
        "--aplicar-traducciones",
        action="store_true",
        help="solo aplica data/traducciones.json al catálogo",
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
    seed = sub.add_parser(
        "exportar-catalogo",
        help="guarda el catálogo (sin datos privados) para incluirlo en la app",
    )
    seed.add_argument(
        "--salida",
        type=Path,
        default=None,
        help="ruta del .sqlite.gz (por defecto catalogo/catalogo-base.sqlite.gz)",
    )
    seed.add_argument(
        "--con-imdb",
        action="store_true",
        help="conserva las notas de IMDb (solo para uso personal; no lo repartas)",
    )
    packs = sub.add_parser(
        "packs",
        help="abre el editor de packs: listas propias de películas conocidas y no conocidas",
    )
    packs.add_argument("--puerto", type=int, default=None, help="puerto local (por defecto 8766)")
    packs.add_argument(
        "--sin-navegador", action="store_true", help="no abre la página en el navegador"
    )
    sub.add_parser(
        "netflix-login",
        help="abre el navegador de la app para iniciar sesión en Netflix (ciérralo al terminar)",
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
    if command == "pistas":
        return _cmd_clues(config, args)
    if command == "plataformas":
        return _cmd_platforms(config, args)
    if command == "exportar-plataformas":
        return _cmd_export_platforms(config, args)
    if command == "exportar-catalogo":
        return _cmd_export_seed(config, args)

    if command == "packs":
        return _cmd_packs(config, args)

    if command == "servir":
        return _cmd_serve(config)
    if command == "netflix-login":
        return _cmd_netflix_login(config)

    print(f"El comando «{command}» no existe.")
    return 1


def _cmd_serve(config: AppConfig) -> int:
    from filmguess.catalog.seed import SeedError
    from filmguess.game.scoreboard import ScoreboardError
    from filmguess.server.run import serve

    try:
        serve(config)
    except ScoreboardError as exc:
        print(f"Error en el marcador: {exc.message}")
        return 1
    except SeedError as exc:
        print(f"Error: {exc}")
        return 1
    except OSError as exc:
        print(f"No se pudo arrancar el servidor en el puerto {config.server.port}: {exc}")
        return 1
    return 0


def _cmd_packs(config: AppConfig, args: argparse.Namespace) -> int:
    from filmguess.catalog.seed import SeedError
    from filmguess.packs.app import DEFAULT_PORT, serve_packs

    port = args.puerto or DEFAULT_PORT
    try:
        serve_packs(config, port=port, open_browser=not args.sin_navegador)
    except SeedError as exc:
        print(f"Error: {exc}")
        return 1
    except OSError as exc:
        print(f"No se pudo arrancar el editor de packs en el puerto {port}: {exc}")
        return 1
    return 0


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


def _cmd_clues(config: AppConfig, args: argparse.Namespace) -> int:
    from filmguess.catalog.clues_fetch import CluesProgress
    from filmguess.catalog.service import (
        run_apply_translations,
        run_clues,
        run_export_untranslated,
    )
    from filmguess.catalog.translations import TranslationsError

    if args.exportar_sin_traducir:
        n = run_export_untranslated(config, args.exportar_sin_traducir)
        print(f"{n} textos sin traducir en {args.exportar_sin_traducir}")
        return 0
    if args.aplicar_traducciones:
        try:
            print(f"Traducciones aplicadas: {run_apply_translations(config)}")
        except TranslationsError as exc:
            print(f"Error en las traducciones: {exc}")
            return 1
        return 0

    def progress(p: CluesProgress) -> None:
        if p.stage != "tmdb" or p.done % 200 == 0 or p.done == p.total:
            print(f"  {p.stage} {p.done}/{p.total} · errores {p.errors}", flush=True)

    try:
        summary = run_clues(config, refresh=args.todas, limit=args.limite, on_progress=progress)
    except (TmdbError, TranslationsError) as exc:
        print(f"Error al descargar las pistas: {exc}")
        return 1
    print(
        f"Pistas: {summary.done - summary.errors} películas · frases de Wikiquote "
        f"{summary.quotes} · críticas {summary.reviews} · eslóganes {summary.taglines} · "
        f"sinopsis {summary.synopses} · director {summary.directors} · "
        f"errores {summary.errors}"
    )
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


def _cmd_export_seed(config: AppConfig, args: argparse.Namespace) -> int:
    from filmguess.catalog.seed import SEED_PATH, SeedError, export_seed
    from filmguess.catalog.service import db_path

    try:
        summary = export_seed(db_path(config), args.salida or SEED_PATH, keep_imdb=args.con_imdb)
    except SeedError as exc:
        print(f"Error: {exc}")
        return 1
    print(
        f"Catálogo exportado en {summary.path}: {summary.movies} películas, "
        f"{summary.clues} con pistas, {summary.size_bytes / 1_000_000:.1f} MB."
    )
    if not args.con_imdb:
        print("Sin notas de IMDb: el juego elegirá las películas con los votos de TMDB.")
    return 0


def _cmd_netflix_login(config: AppConfig) -> int:
    import asyncio

    from filmguess.player.base import PlayerError
    from filmguess.player.browser import open_for_login

    print("Inicia sesión en Netflix en la ventana que se abre y elige el perfil.")
    print("Cuando termines, cierra la ventana: la sesión queda guardada para la app.")
    try:
        asyncio.run(
            open_for_login(
                config.browser,
                config.resolve(config.browser.profile_dir),
                "https://www.netflix.com/login",
            )
        )
    except PlayerError as exc:
        print(f"Error: {exc.message}")
        return 1
    print("Listo.")
    return 0
