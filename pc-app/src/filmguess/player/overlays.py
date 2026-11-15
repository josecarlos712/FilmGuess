"""CSS que oculta la interfaz de Netflix, capas propias (carga y «Revelar») y script de inicio."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from filmguess.webfiles import design_tokens

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
"""

FONTS_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400..700"
    "&family=Figtree:wght@400..600&family=Martian+Mono:wght@400..500&display=swap');"
)
"""Las mismas fuentes que la tele; ``@import`` tiene que ir al principio de la hoja."""

OVERLAY_CSS = """
#fg-cover, #fg-reveal {
  position: fixed; inset: 0; z-index: 2147483647; margin: 0;
  --u: min(1vw, calc(100vh / 56.25));
  background: var(--p-bg); color: var(--p-ink);
  font-family: var(--font-text); -webkit-font-smoothing: antialiased;
}
#fg-cover { display: grid; place-content: center; justify-items: center; gap: calc(var(--u) * 3); }
#fg-cover p {
  margin: 0; font-family: var(--font-display); font-weight: var(--display-weight);
  font-size: calc(var(--u) * 4.4); letter-spacing: var(--display-tracking);
}
#fg-cover .fg-bar {
  position: relative; overflow: hidden; width: calc(var(--u) * 22); height: 3px;
  border-radius: 3px; background: var(--p-line);
}
#fg-cover .fg-bar::after {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 30%; border-radius: inherit;
  background: var(--p-accent); animation: fg-bar 2800ms var(--ease-out) infinite;
}
@keyframes fg-bar { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
#fg-reveal {
  display: grid; place-items: center; padding: calc(var(--u) * 5);
  background: rgb(17 17 17 / 0.92);
  animation: fg-in var(--enter-ms) var(--ease-out) both;
}
#fg-reveal .fg-reveal-inner {
  display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center;
  gap: calc(var(--u) * 5); width: 100%; max-width: calc(var(--u) * 86);
}
#fg-reveal .fg-no-poster { grid-template-columns: minmax(0, 1fr); text-align: center; }
#fg-reveal img {
  height: calc(var(--u) * 42); max-height: 78vh; aspect-ratio: 2 / 3; object-fit: cover;
  border-radius: var(--radius-m);
  box-shadow: 0 calc(var(--u) * 2) calc(var(--u) * 5) rgb(0 0 0 / .55);
}
#fg-reveal h1 {
  margin: 0; font-family: var(--font-display); font-weight: var(--display-weight);
  font-size: calc(var(--u) * 6.4); line-height: 1; letter-spacing: var(--display-tracking-xl);
  text-wrap: balance;
}
#fg-reveal .fg-original {
  margin: calc(var(--u) * 1.6) 0 0; font-size: calc(var(--u) * 2.4); color: var(--p-muted);
  font-style: italic;
}
#fg-reveal .fg-year {
  margin: calc(var(--u) * 2.4) 0 0; font-family: var(--font-mono);
  font-size: calc(var(--u) * 2.6); color: var(--p-accent); letter-spacing: -0.02em;
}
#fg-hints {
  position: fixed; left: calc(var(--u) * 2.4); bottom: calc(var(--u) * 2.4);
  z-index: 2147483646; margin: 0; box-sizing: border-box;
  --u: min(1vw, calc(100vh / 56.25));
  width: max-content; max-width: calc(var(--u) * 34); max-height: 46vh; overflow: hidden;
  padding: calc(var(--u) * 1.4) calc(var(--u) * 1.7);
  border-radius: var(--radius-m); background: rgb(17 17 17 / 0.78);
  -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
  color: var(--p-ink); font-family: var(--font-text); -webkit-font-smoothing: antialiased;
  animation: fg-in var(--enter-ms) var(--ease-out) both;
}
#fg-hints .fg-hints-head {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: calc(var(--u) * 1);
}
#fg-hints p { margin: 0; }
#fg-hints .fg-value {
  font-family: var(--font-display); font-weight: var(--display-weight);
  font-size: calc(var(--u) * 2.3); letter-spacing: var(--display-tracking); color: var(--p-accent);
}
#fg-hints .fg-chip { font-size: calc(var(--u) * 1.7); color: var(--p-muted); }
#fg-hints ol {
  display: grid; gap: calc(var(--u) * 0.9); margin: calc(var(--u) * 1.1) 0 0; padding: 0;
  list-style: none;
}
#fg-hints li { display: grid; gap: calc(var(--u) * 0.2); }
#fg-hints .fg-hint-label {
  font-size: calc(var(--u) * 1.7); color: var(--p-muted);
}
#fg-hints .fg-hint-text {
  font-size: calc(var(--u) * 2.1); line-height: 1.25; text-wrap: pretty;
}
#fg-hints .fg-long { font-size: calc(var(--u) * 1.8); font-style: italic; }
@keyframes fg-in {
  from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  #fg-reveal, #fg-hints, #fg-cover .fg-bar::after { animation: none; }
}
"""
"""Cubierta de carga y capa de «Revelar», con los tokens de ``web/system.css``."""

TEXTS: dict[str, str] = {"cover": "Preparando fotograma…", "skip": "Saltando escena…"}
"""Textos que pinta el script en la página de Netflix."""


def page_css() -> str:
    """Hoja que se inyecta en Netflix: fuentes, ocultación, tokens y capas propias.

    Returns:
        El CSS completo.
    """
    tokens = f"#fg-cover, #fg-reveal, #fg-hints {{ {design_tokens()} }}"
    return "\n".join([FONTS_IMPORT, HIDE_CSS, tokens, OVERLAY_CSS])


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
    config = {"selectors": merged_selectors(selectors), "css": page_css(), "texts": TEXTS}
    return f"window.__fgConfig = {json.dumps(config, ensure_ascii=False)};\n{_api_source()}"
