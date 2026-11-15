"""CSS que oculta la interfaz de Netflix, capa de «Revelar» y script de inicio de página."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

API_JS = Path(__file__).with_name("netflix_api.js")

DEFAULT_SELECTORS: dict[str, str] = {
    "profileGate": ".list-profiles, [data-uia='profile-gate'], .profiles-gate-container",
    "profileLink": ".profile-link, [data-uia='profile-link'], a[data-uia*='profile']",
    "profileName": ".profile-name, [data-uia='profile-name'], [data-uia*='profile-name']",
    "profilePin": "[data-uia='pin-entry'], .pin-number-container",
    "loginLink": "a[href*='/login'], [data-uia='header-login-link']",
    "continueButton": (
        "[data-uia='interrupt-autoplay-continue'], [data-uia='interrupter-continue'],"
        " .postplay-still-container button"
    ),
    "errorView": ".nfp-fatal-error-view, [data-uia='player-error'], .error-page, .nfp-error",
    "unavailableText": (
        "no est[aá] disponible|no disponible en tu (pa[ií]s|regi[oó]n)|not available|"
        "isn'?t available"
    ),
}
"""Selectores de Netflix; se pueden cambiar uno a uno en ``[netflix.selectors]``."""

HIDE_CSS = """
/* Adivina la Peli: nada de Netflix debe delatar la película. */
[data-uia='video-title'], [data-uia*='controls'], [data-uia*='player-skip'],
[data-uia='watch-video-notification'], [class*='PlayerControls'],
.watch-video--bottom-controls-container, .watch-video--back-container,
.watch-video--flag-container, .watch-video--evidence-overlay-container,
.watch-video--skip-content, .watch-video--player-view > div:not(:has(video)),
.evidence-overlay, .advisory, .player-loading,
[data-uia='player-loading-title'] {
  display: none !important; opacity: 0 !important; visibility: hidden !important;
}
html, body, video, * { cursor: none !important; }
#fg-reveal {
  position: fixed; inset: 0; z-index: 2147483647; display: grid; place-items: center;
  background: rgb(10 10 12 / 0.86); animation: fg-in 400ms cubic-bezier(.2,.7,.2,1) both;
  font-family: system-ui, "Segoe UI", sans-serif; color: #f4f1ea;
}
#fg-reveal .fg-reveal-inner { display: flex; gap: 4vw; align-items: center; max-width: 82vw; }
#fg-reveal img { height: 62vh; border-radius: 10px; box-shadow: 0 30px 80px rgb(0 0 0 / .6); }
#fg-reveal h1 { margin: 0; font-size: clamp(36px, 5.5vw, 96px); line-height: 1.02;
  letter-spacing: -0.02em; font-weight: 750; text-wrap: balance; }
#fg-reveal p { margin: 1.2vh 0 0; font-size: clamp(20px, 2.4vw, 40px); opacity: .7; }
@keyframes fg-in { from { opacity: 0; transform: scale(.98); } to { opacity: 1; transform: none; } }
"""


def merged_selectors(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Selectores por defecto con los cambios de la configuración.

    Args:
        overrides: Selectores de ``config.toml`` (los vacíos se ignoran).

    Returns:
        El diccionario completo.
    """
    selectors = dict(DEFAULT_SELECTORS)
    selectors.update({k: v for k, v in (overrides or {}).items() if v})
    return selectors


@cache
def _api_source() -> str:
    return API_JS.read_text(encoding="utf-8")


def init_script(selectors: dict[str, str] | None = None) -> str:
    """Script que se ejecuta en cada página antes que el de Netflix.

    Args:
        selectors: Cambios a los selectores por defecto.

    Returns:
        JavaScript con la configuración y la API de ``netflix_api.js``.
    """
    config = {"selectors": merged_selectors(selectors), "css": HIDE_CSS}
    return f"window.__fgConfig = {json.dumps(config, ensure_ascii=False)};\n{_api_source()}"
