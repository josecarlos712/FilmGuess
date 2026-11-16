"""Juega una partida simulada y guarda capturas de la tele y del móvil para el README.

Arranca el servidor real del juego con el catálogo incluido, pero en lugar de Netflix
usa una «tele» de prueba: un navegador que pinta las mismas capas que se inyectan en
Netflix (cubierta, pistas, avisos, revelar y marcador) sobre un fondo hecho con el
póster desenfocado. Así no se abre Netflix ni salen fotogramas de ninguna película.

El móvil es otro navegador, del tamaño de un teléfono, que juega pulsando los
mismos botones que una persona.

Uso::

    .venv\\Scripts\\python.exe scripts\\capturas_readme.py

Deja las capturas en ``docs/capturas/``. Necesita Edge (o Chrome con ``--canal chrome``)
e internet para las fuentes y los pósters.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc-app" / "src"))

import uvicorn
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.seed import SEED_PATH
from filmguess.catalog.tmdb import poster_url
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.player.base import PlayerStatus, RevealInfo
from filmguess.player.overlays import init_script
from filmguess.server.app import create_app
from playwright.async_api import Browser, Page, async_playwright

OUT = ROOT / "docs" / "capturas"
PORT = 8797
TOKEN = "capturas-readme"
MOVIE = 70305903  # Interstellar: tiene todas las pistas y una frase en español
PLAYERS = ["Lucía", "Marcos", "Carmen", "Dani"]
TV_SIZE = {"width": 1920, "height": 1080}
PHONE = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 2,
    "is_mobile": True,
    "has_touch": True,
}

SCENE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; height: 100%; overflow: hidden; background: #05070b; }}
.scene {{ position: fixed; inset: -6%; background: url('{poster}') center 30% / cover;
  filter: blur(26px) saturate(1.15) brightness(0.62); transform: scale(1.1); }}
.grade {{ position: fixed; inset: 0;
  background: radial-gradient(ellipse at 55% 40%, transparent 30%, rgb(0 0 0 / .55)); }}
</style></head><body><div class="scene"></div><div class="grade"></div>
<video muted></video></body></html>"""
"""Fondo de la «escena»: el póster desenfocado, como un fotograma fuera de foco."""


class TvPlayer:
    """Reproductor que pinta las capas del juego en una página de prueba, sin Netflix."""

    def __init__(self, browser: Browser, db: CatalogDB) -> None:
        self.browser = browser
        self.db = db
        self.page: Page | None = None

    async def open(self) -> Page:
        context = await self.browser.new_context(viewport=TV_SIZE)
        await context.add_init_script(init_script())

        async def scene(route: Any) -> None:
            netflix_id = int(route.request.url.split("/watch/")[1].split("?")[0])
            movie = self.db.get(netflix_id)
            poster = poster_url(movie.poster_path if movie else None, "w780") or ""
            await route.fulfill(
                body=SCENE.format(poster=poster), content_type="text/html"
            )

        await context.route("https://www.netflix.com/watch/**", scene)
        self.page = await context.new_page()
        await self.page.goto(f"http://127.0.0.1:{PORT}/tv")
        return self.page

    async def _fg(self, name: str, arg: Any = None) -> None:
        assert self.page is not None
        await self.page.evaluate("([n, a]) => window.__fg[n](a)", [name, arg])

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def open_login(self) -> None: ...

    async def status(self) -> PlayerStatus:
        return PlayerStatus(browser=True, netflix_session=True)

    async def play(self, netflix_id: int, seconds: int) -> float:
        assert self.page is not None
        await self.page.goto(f"https://www.netflix.com/watch/{netflix_id}?t={seconds}")
        await asyncio.sleep(2.6)  # la cubierta «Preparando fotograma…» se ve un momento
        await shot(self.page, "tv-preparando")
        await self._fg("uncover")
        return seconds + 0.4

    async def pause(self) -> None: ...
    async def resume(self) -> None: ...

    async def cover(self, message: str | None = None) -> None:
        await self._fg("cover", message)

    async def show_hints(self, data: dict[str, Any]) -> None:
        await self._fg("showHints", data)

    async def reveal(self, info: RevealInfo) -> None:
        await self._fg(
            "showReveal",
            {
                "title": info.title,
                "year": info.year,
                "poster": info.poster_url,
                "original": info.original_title,
            },
        )

    async def notify(self, data: dict[str, Any]) -> None:
        await self._fg("toast", {**data, "ms": 6000})

    async def show_scores(self, data: dict[str, Any]) -> None:
        await self._fg("showScores", data)

    async def idle(self) -> None:
        assert self.page is not None
        await self.page.goto(f"http://127.0.0.1:{PORT}/tv")


async def shot(page: Page, name: str) -> None:
    """Guarda una captura en ``docs/capturas``.

    Args:
        page: Página que se captura.
        name: Nombre del fichero, sin extensión.
    """
    path = OUT / f"{name}.jpg"
    await page.screenshot(path=str(path), type="jpeg", quality=86)
    print("  ", path.relative_to(ROOT))


async def api(page: Page, method: str, path: str, body: Any = None) -> Any:
    """Llama a la API del PC desde el móvil, como hace la web del móvil."""
    return await page.evaluate(
        """async ([m, p, b, t]) => {
            const r = await fetch(p, {method: m, headers: {"X-Token": t,
              "Content-Type": "application/json"}, body: b === null ? undefined : JSON.stringify(b)});
            return r.json();
        }""",
        [method, path, body, TOKEN],
    )


async def play(channel: str) -> None:
    """Partida completa: jugadores, búsqueda, momento, pistas, fallo, revelar y puntos."""
    work = Path(tempfile.mkdtemp(prefix="filmguess-capturas-"))
    (work / "catalogo.sqlite").write_bytes(gzip.decompress(SEED_PATH.read_bytes()))
    db = CatalogDB(work / "catalogo.sqlite")
    OUT.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel=channel)
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
        phone_ctx = await browser.new_context(**PHONE)
        phone = await phone_ctx.new_page()
        await phone.goto(f"http://127.0.0.1:{PORT}/m/#token={TOKEN}")
        await phone.wait_for_selector("#view-buscar:not(.hidden)")

        # Marcador de partidas anteriores, para que la tele no salga vacía.
        print("Tele en espera")
        ids: dict[str, str] = {}
        for name in PLAYERS:
            data = await api(phone, "POST", "/players", {"nombre": name})
            ids[name] = next(j["id"] for j in data["jugadores"] if j["nombre"] == name)
        for name, points in zip(PLAYERS, [7, 9, 4, 6], strict=True):
            await api(phone, "POST", f"/players/{ids[name]}/points", {"puntos": points})
        await asyncio.sleep(2.5)
        await shot(tv_page, "tv-espera")

        print("Móvil: buscar y elegir el momento")
        await phone.fill("#q", "amor")  # una búsqueda con muchos pósters
        await phone.wait_for_selector("#resultados .poster-card")
        await phone.wait_for_timeout(1500)
        await phone.wait_for_function(
            "[...document.querySelectorAll('img.poster')].slice(0, 6).every(i => i.complete)"
        )
        await phone.wait_for_timeout(900)
        await shot(phone, "movil-buscar")
        await phone.fill("#q", "interstellar")
        await phone.wait_for_timeout(1200)
        await phone.click("#resultados .poster-card")
        await phone.wait_for_selector("#view-tiempo:not(.hidden)")
        await phone.wait_for_timeout(1200)
        await shot(phone, "movil-momento")

        print("Ronda")
        await phone.click("#btn-mostrar")
        await phone.wait_for_selector("#view-ronda:not(.hidden)", timeout=30_000)
        await phone.click("#btn-pista")
        await phone.wait_for_timeout(500)
        await phone.click("#btn-pista")
        await phone.wait_for_timeout(1500)
        await shot(tv_page, "tv-pistas")
        await shot(phone, "movil-ronda")

        # Marcos se arriesga y falla: aviso en la esquina de la tele.
        await api(phone, "POST", "/fail", {"jugadores": [ids["Marcos"]]})
        await asyncio.sleep(1.2)
        await shot(tv_page, "tv-aviso")

        print("Revelar y repartir puntos")
        await phone.click("#btn-revelar")
        await phone.wait_for_selector("#acierto:not(.hidden)")
        for name in ("Lucía", "Carmen"):
            await phone.click(f"#acierto-jugadores .chip:has-text('{name}')")
        await phone.click("#btn-dar")
        await phone.wait_for_timeout(450)
        await shot(phone, "movil-acierto")
        await asyncio.sleep(6.8)  # ya se ha ido el aviso de la esquina
        await shot(tv_page, "tv-revelar")

        await phone_ctx.close()
        server.should_exit = True
        await serving
        await browser.close()
    db.close()
    shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    """Punto de entrada.

    Returns:
        Código de salida.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--canal", default="msedge", help="msedge (por defecto) o chrome"
    )
    args = parser.parse_args()
    asyncio.run(play(args.canal))
    print(
        json.dumps(
            {"capturas": sorted(p.name for p in OUT.glob("*.jpg"))}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
