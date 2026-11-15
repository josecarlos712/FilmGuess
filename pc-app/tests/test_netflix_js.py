"""Integración: ``netflix_api.js`` en un Edge real sin ventana, contra un Netflix simulado.

Las páginas de netflix.com se sirven desde el propio test (``page.route``), así que no se
conecta a Netflix. Si Edge no está instalado, se omite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from playwright.async_api import Route

from filmguess.config import BrowserConfig
from filmguess.player.base import PlayerError, RevealInfo
from filmguess.player.browser import Browser
from filmguess.player.netflix import NetflixPlayer
from filmguess.player.overlays import init_script

WATCH_OK = """<!doctype html><html><body>
<div class="watch-video--player-view"><video></video>
<div data-uia="controls-standard">controles</div></div>
<div data-uia="video-title">Origen</div>
<script>
  const v = document.querySelector("video");
  Object.defineProperty(v, "readyState", { get: () => 4 });
  let t = 12000, paused = false;
  const p = { getCurrentTime: () => t, seek: (ms) => { t = ms; }, pause: () => { paused = true; },
    play: () => { paused = false; }, isPaused: () => paused, getDuration: () => 8880000 };
  window.netflix = { appContext: { state: { playerApp: { getAPI: () => ({ videoPlayer: {
    getAllPlayerSessionIds: () => ["watch-1"], getVideoPlayerBySessionId: () => p } }) } } } };
</script></body></html>"""

UNAVAILABLE = """<!doctype html><html><body><div class="nfp-fatal-error-view">
Este título no está disponible en tu país. Código M7111-1331</div></body></html>"""

LOGGED_OUT = """<!doctype html><html><body><header>
<a href="/es/login" data-uia="header-login-link">Iniciar sesión</a></header>
<h1>Morir todavía</h1></body></html>"""

PROFILES = """<!doctype html><html><body><div class="list-profiles">
<a class="profile-link" href="/watch/1"><span class="profile-name">Niños</span></a>
<a class="profile-link" href="/watch/1?t=600"><span class="profile-name">Ana</span></a>
</div></body></html>"""


async def fake_netflix(route: Route) -> None:
    url = route.request.url
    if "/watch/1" in url:
        body = WATCH_OK
    elif "/watch/2" in url:
        body = UNAVAILABLE
    elif "/watch/3" in url:
        body = PROFILES
    elif "/watch/4" in url:
        body = '<script>location.replace("/es/title/4?fromWatch=true")</script>'
    elif "/es/title/4" in url:
        body = LOGGED_OUT
    else:
        body = "<!doctype html><html><body>espera</body></html>"
    await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


@pytest.fixture
async def player(tmp_path: Path) -> AsyncIterator[NetflixPlayer]:
    config = BrowserConfig(channel="msedge", fullscreen=False)
    browser = Browser(config, tmp_path / "perfil", init_script(), headless=True)
    try:
        page = await browser.page()
    except PlayerError as exc:
        pytest.skip(f"Edge no disponible: {exc.message}")
    await page.route("https://www.netflix.com/**", fake_netflix)
    await page.route("https://fg.test/**", fake_netflix)
    netflix = NetflixPlayer(
        browser, idle_url="https://fg.test/tv", profile_name="Ana", timeout_s=10
    )
    yield netflix
    await netflix.close()


async def test_play_salta_pausa_y_oculta_la_interfaz(player: NetflixPlayer) -> None:
    actual = await player.play(1, 600)
    page = await player.browser.page()

    assert actual == pytest.approx(600, abs=0.01)
    state = await page.evaluate("window.__fg.state()")
    assert state["paused"] is True
    hidden = await page.evaluate(
        """() => ["[data-uia='video-title']", "[data-uia='controls-standard']"].map(
            (s) => getComputedStyle(document.querySelector(s)).display)"""
    )
    assert hidden == ["none", "none"]
    assert await page.evaluate("document.getElementById('fg-cover')") is None


async def test_cubierta_tapa_el_video_mientras_carga(player: NetflixPlayer) -> None:
    page = await player.browser.page()
    await page.goto("https://www.netflix.com/watch/1?t=0")

    text = await page.evaluate("document.getElementById('fg-cover').innerText")
    assert text == "Preparando fotograma…"
    await page.evaluate("window.__fg.uncover()")
    assert await page.evaluate("document.getElementById('fg-cover')") is None


async def test_revelar_muestra_titulo_y_anio(player: NetflixPlayer) -> None:
    await player.play(1, 30)
    await player.reveal(RevealInfo("Origen", 2010, None, "Inception"))
    page = await player.browser.page()

    text = await page.evaluate("document.getElementById('fg-reveal').innerText")
    assert "Origen" in text and "Inception" in text and "2010" in text
    color = await page.evaluate(
        "getComputedStyle(document.querySelector('#fg-reveal .fg-year')).color"
    )
    assert color == "rgb(160, 134, 114)"  # --p-accent de web/system.css

    await player.resume()
    assert await page.evaluate("document.getElementById('fg-reveal')") is None


async def test_no_disponible(player: NetflixPlayer) -> None:
    with pytest.raises(PlayerError) as exc:
        await player.play(2, 10)
    assert exc.value.code == "no_disponible_es"


async def test_elige_perfil_por_nombre_sin_acentos_ni_mayusculas(player: NetflixPlayer) -> None:
    assert await player.play(3, 600) == pytest.approx(600, abs=0.01)
    page = await player.browser.page()
    assert "/watch/1" in page.url


async def test_idle_vuelve_a_la_pantalla_de_espera(player: NetflixPlayer) -> None:
    await player.play(1, 10)
    await player.idle()
    page = await player.browser.page()

    assert page.url == "https://fg.test/tv"


async def test_sin_sesion_redirige_a_la_pagina_publica(player: NetflixPlayer) -> None:
    with pytest.raises(PlayerError) as exc:
        await player.play(4, 10)
    assert exc.value.code == "sesion_caducada", exc.value.message
