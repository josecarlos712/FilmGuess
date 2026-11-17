"""Capturas del modo «cada uno responde desde su móvil» con una partida simulada.

Usa la misma tele de prueba que ``capturas_readme.py`` (sin Netflix). Deja las
capturas en ``docs/capturas/`` con el prefijo ``jugar-``.

Uso::

    .venv\\Scripts\\python.exe scripts\\capturas_jugar.py
"""

from __future__ import annotations

import asyncio
import gzip
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from capturas_readme import MOVIE, PHONE, PLAYERS, PORT, TOKEN, TvPlayer, api, shot
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.seed import SEED_PATH
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.server.app import create_app
from playwright.async_api import BrowserContext, Page, async_playwright


async def player_phone(ctx: BrowserContext, name: str) -> Page:
    """Abre la página del jugador y elige su nombre."""
    page = await ctx.new_page()
    await page.goto(f"http://127.0.0.1:{PORT}/m/jugar.html#token={TOKEN}")
    await page.wait_for_selector("#view-quien:not(.hidden)")
    await page.wait_for_selector(f"#quien-jugadores .chip:has-text('{name}')")
    return page


async def answer(page: Page, text: str) -> None:
    """Escribe el título y lo comprueba."""
    await page.fill("#respuesta", text)
    await page.click("#btn-comprobar")
    await page.wait_for_selector("#resultado:not(.hidden)")
    await page.wait_for_timeout(400)


async def main() -> None:
    """Partida: invitación, quién eres, espera, ronda, casi, acierto, fallo y revelar."""
    work = Path(tempfile.mkdtemp(prefix="filmguess-capturas-"))
    (work / "catalogo.sqlite").write_bytes(gzip.decompress(SEED_PATH.read_bytes()))
    db = CatalogDB(work / "catalogo.sqlite")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="msedge")
        tv = TvPlayer(browser, db)
        session = GameSession(
            db,
            tv,
            Scoreboard(work / "scoreboard.json"),
            RandomConfig(min_year=1991, min_votes=1000, pool_size=50, no_repeat=0),
        )
        app = create_app(session, TOKEN, port=PORT, ip="192.168.1.50")
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
        )
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)

        tv_page = await tv.open()
        host_ctx = await browser.new_context(**PHONE)
        host = await host_ctx.new_page()
        await host.goto(f"http://127.0.0.1:{PORT}/m/#token={TOKEN}")
        await host.wait_for_selector("#view-buscar:not(.hidden)")
        ids: dict[str, str] = {}
        for name in PLAYERS:
            data = await api(host, "POST", "/players", {"nombre": name})
            ids[name] = next(j["id"] for j in data["jugadores"] if j["nombre"] == name)
        for name, points in zip(PLAYERS, [7, 9, 4, 6], strict=True):
            await api(host, "POST", f"/players/{ids[name]}/points", {"puntos": points})

        print("Móvil que dirige: invitación")
        await host.click(".step[data-view='jugadores']")
        await host.click("#btn-invitar")
        await host.wait_for_selector("#invitacion:not(.hidden) svg")
        await host.wait_for_timeout(500)
        await host.evaluate("document.getElementById('btn-invitar').scrollIntoView({block: 'center'})")
        await shot(host, "jugar-invitacion")

        print("Jugadores: quién eres y espera")
        phones: dict[str, Page] = {}
        for name in ("Lucía", "Marcos", "Carmen"):
            ctx = await browser.new_context(**PHONE)
            phones[name] = await player_phone(ctx, name)
        await phones["Lucía"].wait_for_timeout(800)
        await shot(phones["Lucía"], "jugar-quien")
        for name, page in phones.items():
            await page.click(f"#quien-jugadores .chip:has-text('{name}')")
            await page.wait_for_selector("#view-juego:not(.hidden)")
        await phones["Lucía"].wait_for_timeout(500)
        await shot(phones["Lucía"], "jugar-espera")

        print("Ronda con dos pistas")
        await api(host, "POST", "/play", {"netflixId": MOVIE, "seconds": 3600})
        await api(host, "POST", "/hint")
        await api(host, "POST", "/hint")
        await phones["Lucía"].wait_for_selector("#jugando:not(.hidden)")
        await phones["Lucía"].fill("#respuesta", "interestelar")
        await phones["Lucía"].wait_for_timeout(600)
        await shot(phones["Lucía"], "jugar-ronda")

        print("Casi, acierto y fallo")
        await answer(phones["Carmen"], "interestelaria")
        await shot(phones["Carmen"], "jugar-casi")
        await answer(phones["Lucía"], "interestelar")
        await shot(phones["Lucía"], "jugar-acierto")
        await asyncio.sleep(0.8)
        await shot(tv_page, "jugar-tv-acierto")
        await answer(phones["Marcos"], "Gravity")
        await shot(phones["Marcos"], "jugar-fallo")

        print("Revelar")
        await api(host, "POST", "/reveal")
        await phones["Carmen"].wait_for_selector("#resultado.near, #resultado.bad")
        await phones["Carmen"].wait_for_timeout(800)
        await shot(phones["Carmen"], "jugar-revelada")

        server.should_exit = True
        await serving
        await browser.close()
    db.close()
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
