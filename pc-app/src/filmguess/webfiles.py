"""Ubicación de las páginas web (``pc-app/web``) y tokens del sistema de diseño."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
"""Carpeta con ``system.css``, ``tv/`` y ``panel/``."""

MOBILE_DIR = Path(__file__).resolve().parents[3] / "mobile-app"
"""Web del móvil (``/m/``), en la raíz del repositorio."""

SYSTEM_CSS = WEB_DIR / "system.css"
COVER_CSS = WEB_DIR / "cover.css"
TV_PAGE = WEB_DIR / "tv" / "index.html"
PANEL_PAGE = WEB_DIR / "panel" / "index.html"

_TOKENS = re.compile(r"/\* tokens:start \*/\s*:root\s*\{(?P<body>.*?)\}\s*/\* tokens:end \*/", re.S)


class WebFilesError(RuntimeError):
    """Faltan las páginas web o ``system.css`` no tiene el bloque de tokens."""


@cache
def design_tokens() -> str:
    """Declaraciones de los tokens de ``system.css`` (sin el selector ``:root``).

    Returns:
        Las declaraciones ``--p-…: valor;`` tal como están en el fichero.

    Raises:
        WebFilesError: Si no existe ``system.css`` o no tiene los marcadores de tokens.
    """
    try:
        text = SYSTEM_CSS.read_text(encoding="utf-8")
    except OSError as exc:
        raise WebFilesError(f"No se puede leer {SYSTEM_CSS}: {exc}") from exc
    match = _TOKENS.search(text)
    if not match:
        raise WebFilesError(f"{SYSTEM_CSS} no tiene el bloque «tokens:start … tokens:end».")
    return match.group("body").strip()


@cache
def cover_css() -> str:
    """Hoja de la cubierta «Preparando fotograma…», compartida por /tv y Netflix.

    Returns:
        El contenido de ``cover.css``.

    Raises:
        WebFilesError: Si no se puede leer ``cover.css``.
    """
    try:
        return COVER_CSS.read_text(encoding="utf-8")
    except OSError as exc:
        raise WebFilesError(f"No se puede leer {COVER_CSS}: {exc}") from exc
