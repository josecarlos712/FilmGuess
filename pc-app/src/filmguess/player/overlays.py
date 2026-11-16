"""CSS que oculta la interfaz de Netflix, capas propias (carga y «Revelar») y script de inicio."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from filmguess.webfiles import cover_css, design_tokens

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

EASINGS = (
    "--fg-out: cubic-bezier(0.19, 1, 0.22, 1); --fg-inout: cubic-bezier(0.645, 0.045, 0.355, 1);"
)
"""Curvas de las capas: ease-out expo para entrar y ease-in-out cubic para moverse."""

OVERLAY_CSS = """
#fg-reveal, #fg-hints, #fg-toasts, #fg-scores { margin: 0; --u: min(1vw, calc(100vh / 56.25)); }
#fg-reveal {
  position: fixed; inset: 0; z-index: 2147483647;
  background: var(--p-bg); color: var(--p-ink);
  font-family: var(--font-text); -webkit-font-smoothing: antialiased;
}
#fg-reveal {
  display: grid; place-items: center; padding: calc(var(--u) * 5);
  background: rgb(17 17 17 / 0.92); overflow: hidden; isolation: isolate;
  animation: fg-fade 420ms var(--fg-out) both;
}
#fg-reveal::before {
  content: ""; position: absolute; left: 50%; top: 50%; z-index: -1;
  width: calc(var(--u) * 90); aspect-ratio: 1;
  margin: calc(var(--u) * -45) 0 0 calc(var(--u) * -45);
  border-radius: 50%; pointer-events: none;
  background: radial-gradient(
    closest-side, color-mix(in srgb, var(--p-accent) 38%, transparent), transparent
  );
  animation: fg-burst 1400ms var(--fg-out) both;
}
#fg-reveal .fg-reveal-inner {
  display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center;
  gap: calc(var(--u) * 5); width: 100%; max-width: calc(var(--u) * 86);
}
#fg-reveal .fg-no-poster { grid-template-columns: minmax(0, 1fr); text-align: center; }
#fg-reveal .fg-poster {
  position: relative; overflow: hidden; border-radius: var(--radius-m);
  box-shadow: 0 calc(var(--u) * 2) calc(var(--u) * 5) rgb(0 0 0 / .55);
  animation: fg-poster 1100ms var(--fg-out) 80ms both;
}
#fg-reveal .fg-poster::after {
  /* destello que cruza el póster una vez */
  content: ""; position: absolute; inset: -20% auto -20% 0; width: 45%;
  background: linear-gradient(100deg, transparent, rgb(255 255 255 / 0.28), transparent);
  transform: translateX(-120%) skewX(-12deg);
  animation: fg-sheen 1200ms var(--fg-inout) 700ms both;
}
#fg-reveal img {
  display: block; height: calc(var(--u) * 42); max-height: 78vh; aspect-ratio: 2 / 3;
  object-fit: cover;
}
#fg-reveal .fg-word {
  display: inline-block;
  animation: fg-rise 800ms var(--fg-out) calc(260ms + var(--i, 0) * 70ms) both;
}
#fg-reveal .fg-original { animation: fg-rise 800ms var(--fg-out) 520ms both; }
#fg-reveal .fg-year { animation: fg-rise 800ms var(--fg-out) 640ms both; }
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
  position: fixed; left: calc(var(--u) * 2); bottom: calc(var(--u) * 2);
  z-index: 2147483646; margin: 0; box-sizing: border-box;
  --u: min(1vw, calc(100vh / 56.25));
  width: max-content; min-width: calc(var(--u) * 18); max-width: calc(var(--u) * 31);
  padding: calc(var(--u) * 1.1) calc(var(--u) * 1.4);
  border-radius: var(--radius-m); background: rgb(17 17 17 / 0.72);
  -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
  color: var(--p-ink); font-family: var(--font-text); -webkit-font-smoothing: antialiased;
  animation: fg-in var(--enter-ms) var(--ease-out) both;
}
#fg-hints .fg-hints-head {
  display: flex; flex-wrap: wrap; align-items: baseline; column-gap: calc(var(--u) * 1.2);
}
#fg-hints p { margin: 0; }
#fg-hints .fg-value {
  font-family: var(--font-display); font-weight: var(--display-weight);
  font-size: calc(var(--u) * 2.1); letter-spacing: var(--display-tracking); color: var(--p-accent);
}
#fg-hints .fg-chip { font-size: calc(var(--u) * 1.7); color: var(--p-muted); }
#fg-hints ol {
  display: grid; grid-template-columns: auto minmax(0, 1fr);
  column-gap: calc(var(--u) * 1.2); row-gap: calc(var(--u) * 0.5);
  margin: calc(var(--u) * 0.9) 0 0; padding: calc(var(--u) * 0.9) 0 0;
  border-top: 1px solid rgb(255 255 255 / 0.12); list-style: none;
}
#fg-hints li { display: contents; }
#fg-hints li > span {
  animation: fg-in 520ms var(--fg-out) calc(180ms + var(--i, 0) * 90ms) both;
}
#fg-hints .fg-hint-label {
  font-size: calc(var(--u) * 1.7); line-height: 1.35; color: var(--p-muted);
  align-self: baseline;
}
#fg-hints .fg-hint-text {
  font-size: calc(var(--u) * 1.9); line-height: 1.3; text-wrap: pretty; align-self: baseline;
}
#fg-hints .fg-long-label { grid-column: 1 / -1; margin-top: calc(var(--u) * 0.3); }
#fg-hints .fg-long {
  grid-column: 1 / -1; font-size: calc(var(--u) * 1.7); font-style: italic;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 4; overflow: hidden;
}
@keyframes fg-in {
  from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; }
}
#fg-toasts {
  position: fixed; top: calc(var(--u) * 2); right: calc(var(--u) * 2); z-index: 2147483647;
  display: grid; justify-items: end; gap: calc(var(--u) * 0.8); margin: 0;
  pointer-events: none;
}
#fg-toasts .fg-toast {
  display: flex; align-items: center; gap: calc(var(--u) * 1.2);
  max-width: calc(var(--u) * 30); padding: calc(var(--u) * 0.9) calc(var(--u) * 1.4);
  border-radius: var(--radius-m); background: rgb(17 17 17 / 0.74);
  -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
  box-shadow: inset 3px 0 0 var(--fg-tone, var(--p-accent));
  color: var(--p-ink); font-family: var(--font-text); -webkit-font-smoothing: antialiased;
  animation: fg-toast-in 560ms var(--fg-out) both;
}
#fg-toasts .fg-good { --fg-tone: var(--p-accent); }
#fg-toasts .fg-bad { --fg-tone: var(--p-danger); }
#fg-toasts .fg-info { --fg-tone: var(--p-muted); }
#fg-toasts .fg-toast-out { animation: fg-toast-out 240ms var(--fg-out) forwards; }
#fg-toasts .fg-toast-delta {
  flex: none; font-family: var(--font-mono); font-size: calc(var(--u) * 2.6);
  letter-spacing: -0.04em; color: var(--fg-tone);
  animation: fg-pop 700ms var(--fg-out) 200ms both;
}
#fg-toasts p { margin: 0; }
#fg-toasts .fg-toast-title {
  font-family: var(--font-display); font-weight: var(--display-weight);
  font-size: calc(var(--u) * 1.9); letter-spacing: var(--display-tracking); line-height: 1.15;
}
#fg-toasts .fg-toast-detail {
  margin-top: calc(var(--u) * 0.2); font-size: calc(var(--u) * 1.5); color: var(--p-muted);
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
#fg-scores {
  margin: calc(var(--u) * 3) 0 0; padding-top: calc(var(--u) * 2);
  border-top: 1px solid rgb(255 255 255 / 0.12);
  animation: fg-rise 700ms var(--fg-out) 800ms both;
}
#fg-scores.fg-floating {
  position: fixed; right: calc(var(--u) * 2); bottom: calc(var(--u) * 2);
  z-index: 2147483646; width: calc(var(--u) * 32); margin: 0;
  padding: calc(var(--u) * 1.6) calc(var(--u) * 2); border: 0;
  border-radius: var(--radius-m); background: rgb(17 17 17 / 0.8);
  -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
  color: var(--p-ink); font-family: var(--font-text);
}
#fg-scores .fg-scores-title {
  margin: 0 0 calc(var(--u) * 1); font-size: calc(var(--u) * 1.7); color: var(--p-muted);
  text-transform: uppercase; letter-spacing: 0.12em;
}
#fg-scores ol { display: grid; gap: calc(var(--u) * 0.5); margin: 0; padding: 0; list-style: none; }
#fg-scores li {
  position: relative; isolation: isolate; display: grid;
  grid-template-columns: calc(var(--u) * 2.4) minmax(0, 1fr) auto calc(var(--u) * 4.5);
  align-items: baseline; gap: calc(var(--u) * 1);
  padding: calc(var(--u) * 0.6) calc(var(--u) * 1); font-size: calc(var(--u) * 2);
  animation: fg-in 600ms var(--fg-out) calc(950ms + var(--i, 0) * 110ms) both;
}
#fg-scores .fg-bar {
  position: absolute; inset: 0; z-index: -1; border-radius: var(--radius-s);
  background: rgb(255 255 255 / 0.06); transform-origin: left center;
  transform: scaleX(var(--w, 0));
  animation: fg-grow 1100ms var(--fg-out) calc(1050ms + var(--i, 0) * 110ms) both;
}
#fg-scores .fg-first .fg-bar {
  background: color-mix(in srgb, var(--p-accent) 30%, transparent);
}
#fg-scores .fg-rank, #fg-scores .fg-pts, #fg-scores .fg-delta {
  font-family: var(--font-mono); letter-spacing: -0.03em;
}
#fg-scores .fg-rank { color: var(--p-muted); }
#fg-scores .fg-first .fg-rank, #fg-scores .fg-first .fg-pts { color: var(--p-accent); }
#fg-scores .fg-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
#fg-scores .fg-pts { text-align: right; }
#fg-scores .fg-delta {
  font-size: 0.8em; color: var(--p-accent);
  animation: fg-pop 700ms var(--fg-out) calc(1500ms + var(--i, 0) * 110ms) both;
}
#fg-scores .fg-delta.fg-neg { color: var(--p-danger); }
@keyframes fg-toast-in {
  from { opacity: 0; transform: translateX(calc(100% + var(--u) * 2)); }
  to { opacity: 1; transform: none; }
}
@keyframes fg-toast-out { to { opacity: 0; transform: translateX(24px); } }
@keyframes fg-pop {
  0% { opacity: 0; transform: scale(0.5); } 60% { opacity: 1; transform: scale(1.18); }
  100% { opacity: 1; transform: scale(1); }
}
@keyframes fg-grow { from { transform: scaleX(0); } to { transform: scaleX(var(--w, 0)); } }
@keyframes fg-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes fg-burst {
  from { opacity: 0; transform: scale(0.6); } 40% { opacity: 1; }
  to { opacity: 0.7; transform: scale(1); }
}
@keyframes fg-poster {
  from { opacity: 0; transform: translateY(calc(var(--u) * 4)) rotate(-4deg) scale(0.92); }
  to { opacity: 1; transform: none; }
}
@keyframes fg-sheen { to { transform: translateX(340%) skewX(-12deg); } }
@keyframes fg-rise {
  from { opacity: 0; transform: translateY(0.45em); } to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  #fg-reveal, #fg-reveal::before, #fg-reveal .fg-poster, #fg-reveal .fg-poster::after,
  #fg-reveal .fg-word, #fg-reveal .fg-original, #fg-reveal .fg-year,
  #fg-hints, #fg-hints li > span, #fg-toasts .fg-toast, #fg-toasts .fg-toast-out,
  #fg-toasts .fg-toast-delta, #fg-scores, #fg-scores li, #fg-scores .fg-bar,
  #fg-scores .fg-delta { animation: none; }
  #fg-reveal .fg-poster::after { display: none; }
}
"""
"""Capa de «Revelar» y pistas, con los tokens de ``web/system.css``.

La cubierta «Preparando fotograma…» está en ``web/cover.css`` (la comparte la tele).
"""

TEXTS: dict[str, str] = {"cover": "Preparando fotograma…", "skip": "Saltando escena…"}
"""Textos que pinta el script en la página de Netflix."""


def page_css() -> str:
    """Hoja que se inyecta en Netflix: fuentes, ocultación, tokens y capas propias.

    Returns:
        El CSS completo.
    """
    tokens = (
        f".fg-cover, #fg-reveal, #fg-hints, #fg-toasts, #fg-scores"
        f" {{ {design_tokens()} {EASINGS} }}"
    )
    return "\n".join([FONTS_IMPORT, HIDE_CSS, tokens, cover_css(), OVERLAY_CSS])


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
