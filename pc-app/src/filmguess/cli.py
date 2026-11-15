"""Interfaz de línea de comandos de la app de PC."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from filmguess.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


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
    sub.add_parser("importar", help="importa o actualiza el catálogo desde el CSV y TMDB")
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

    print(f"El comando «{command}» todavía no está implementado.")
    return 1
