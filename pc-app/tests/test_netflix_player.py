"""Tests de la lógica del reproductor de Netflix con un navegador simulado."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from filmguess.player.base import PlayerError, RevealInfo
from filmguess.player.netflix import WATCH_URL, NetflixPlayer
from filmguess.player.overlays import DEFAULT_SELECTORS, init_script, merged_selectors

IDLE = "http://127.0.0.1:8765/tv"


class FakePage:
    """Pestaña que responde a ``__fg.<función>`` con un guion de estados."""

    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = list(states)
        self.urls: list[str] = []
        self.calls: list[tuple[str, Any]] = []
        self.position: float | None = None
        self.seek_works = True
        self.choose_ok = True
        self.goto_error: Exception | None = None
        self.on_goto: Callable[[str], None] | None = None

    @property
    def url(self) -> str:
        return self.urls[-1] if self.urls else "about:blank"

    async def goto(self, url: str, **_: Any) -> None:
        if self.goto_error:
            raise self.goto_error
        self.urls.append(url)
        if self.on_goto:
            self.on_goto(url)

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        name, value = arg
        self.calls.append((name, value))
        if name == "state":
            state = self.states.pop(0) if len(self.states) > 1 else dict(self.states[0])
            if state.get("kind") == "ready" and self.position is not None:
                state["currentTime"] = self.position
            return state
        if name == "seek":
            if self.seek_works:
                self.position = float(value)
            return self.seek_works
        if name == "chooseProfile":
            return self.choose_ok
        return True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.open = True
        self.fullscreens = 0
        self.started = 0

    def is_open(self) -> bool:
        return self.open

    async def start(self) -> None:
        self.started += 1

    async def page(self) -> FakePage:
        if not self.open:
            raise PlayerError("navegador_cerrado", "cerrado")
        return self._page

    async def fullscreen(self) -> None:
        self.fullscreens += 1

    async def close(self) -> None:
        self.open = False


class Clock:
    """Reloj falso: cada espera avanza el tiempo."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def ready(t: float, *, api: bool = True) -> dict[str, Any]:
    return {"kind": "ready", "currentTime": t, "hasApi": api, "paused": False}


def make(
    page: FakePage, *, profile: str = "", timeout: float = 15
) -> tuple[NetflixPlayer, FakeBrowser]:
    browser = FakeBrowser(page)
    clock = Clock()
    player = NetflixPlayer(
        browser,
        idle_url=IDLE,
        profile_name=profile,
        timeout_s=timeout,
        clock=clock,
        sleep=clock.sleep,
    )
    return player, browser


async def test_play_en_su_segundo() -> None:
    page = FakePage([{"kind": "loading"}, {"__missing": True}, ready(5025.6)])
    player, browser = make(page)

    actual = await player.play(70131314, 5025)

    assert actual == 5025.6
    assert page.urls == [WATCH_URL.format(id=70131314, seconds=5025)]
    assert ("pause", None) in page.calls
    assert ("seek", 5025) not in page.calls
    assert browser.fullscreens == 1
    assert (await player.status()).netflix_session is True


async def test_play_corrige_con_seek_si_no_coincide() -> None:
    page = FakePage([ready(0.0)])
    player, _ = make(page)

    actual = await player.play(1, 600)

    assert actual == 600.0
    assert ("seek", 600) in page.calls
    assert len(page.urls) == 1


async def test_play_recarga_si_el_seek_no_funciona() -> None:
    page = FakePage([ready(0.0, api=False)])

    def landed(url: str) -> None:
        if len(page.urls) == 2:
            page.position = 600.4

    page.on_goto = landed
    player, _ = make(page)

    assert await player.play(1, 600) == 600.4
    assert len(page.urls) == 2


async def test_play_tiempo_agotado_si_no_llega_al_segundo() -> None:
    page = FakePage([ready(0.0)])
    page.seek_works = False
    player, _ = make(page, timeout=2)

    with pytest.raises(PlayerError) as exc:
        await player.play(1, 600)
    assert exc.value.code == "tiempo_agotado"


async def test_play_elige_el_perfil_configurado() -> None:
    page = FakePage([{"kind": "profiles", "profiles": ["Ana", "Niños"]}, ready(10)])
    player, _ = make(page, profile="ana")

    assert await player.play(1, 10) == 10
    assert ("chooseProfile", "ana") in page.calls


async def test_play_pide_perfil_si_no_hay_configurado() -> None:
    page = FakePage([{"kind": "profiles", "profiles": ["Ana", "Luis"]}])
    player, _ = make(page)

    with pytest.raises(PlayerError) as exc:
        await player.play(1, 10)
    assert exc.value.code == "perfil_requerido"
    assert "Ana, Luis" in exc.value.message


async def test_play_perfil_inexistente() -> None:
    page = FakePage([{"kind": "profiles", "profiles": ["Ana"]}])
    page.choose_ok = False
    player, _ = make(page, profile="Pepe")

    with pytest.raises(PlayerError, match="profile_name"):
        await player.play(1, 10)


async def test_play_vuelve_a_pedir_la_pelicula_si_netflix_lleva_a_la_portada() -> None:
    page = FakePage([{"kind": "elsewhere", "url": "https://www.netflix.com/browse"}, ready(10)])
    player, _ = make(page)

    assert await player.play(1, 10) == 10
    assert len(page.urls) == 2


async def test_play_pulsa_seguir_viendo() -> None:
    page = FakePage([{"kind": "continue"}, ready(10)])
    player, _ = make(page)

    await player.play(1, 10)

    assert ("clickContinue", None) in page.calls


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ({"kind": "login"}, "sesion_caducada"),
        ({"kind": "pin"}, "perfil_requerido"),
        ({"kind": "unavailable", "message": "No está disponible"}, "no_disponible_es"),
        ({"kind": "error", "code": "M7111"}, "netflix_error"),
        ({"kind": "loading"}, "tiempo_agotado"),
    ],
)
async def test_play_errores(state: dict[str, Any], code: str) -> None:
    player, _ = make(FakePage([state]), timeout=1)

    with pytest.raises(PlayerError) as exc:
        await player.play(1, 10)
    assert exc.value.code == code


async def test_sesion_caducada_se_refleja_en_el_estado() -> None:
    player, _ = make(FakePage([{"kind": "login"}]))

    with pytest.raises(PlayerError):
        await player.play(1, 10)
    assert (await player.status()).netflix_session is False


async def test_error_de_netflix_incluye_el_codigo() -> None:
    player, _ = make(FakePage([{"kind": "error", "code": "M7111"}]))

    with pytest.raises(PlayerError, match="M7111"):
        await player.play(1, 10)


async def test_pagina_que_no_carga() -> None:
    page = FakePage([ready(0)])

    class TimeoutError(Exception):
        pass

    page.goto_error = TimeoutError("Timeout 15000ms exceeded")
    player, _ = make(page)

    with pytest.raises(PlayerError) as exc:
        await player.play(1, 10)
    assert exc.value.code == "tiempo_agotado"


async def test_navegador_cerrado() -> None:
    player, browser = make(FakePage([ready(0)]))
    browser.open = False

    with pytest.raises(PlayerError) as exc:
        await player.play(1, 10)
    assert exc.value.code == "navegador_cerrado"
    assert (await player.status()).browser is False


async def test_pause_resume_reveal_idle_y_start() -> None:
    page = FakePage([ready(0)])
    player, browser = make(page)

    await player.start()
    await player.pause()
    await player.resume()
    await player.reveal(RevealInfo("Origen", 2010, "https://img/p.jpg"))
    await player.idle()

    names = [c[0] for c in page.calls]
    assert names == ["pause", "play", "pause", "showReveal"]
    assert page.calls[-1][1] == {"title": "Origen", "year": 2010, "poster": "https://img/p.jpg"}
    assert page.urls == [IDLE]  # start() solo abre el navegador
    assert browser.started == 1
    await player.close()
    assert browser.open is False


async def test_orden_fuera_de_netflix() -> None:
    page = FakePage([ready(0)])

    async def missing(expression: str, arg: Any = None) -> Any:
        return {"__missing": True}

    page.evaluate = missing  # type: ignore[method-assign]
    player, _ = make(page)

    with pytest.raises(PlayerError) as exc:
        await player.pause()
    assert exc.value.code == "netflix_error"


def test_init_script_lleva_selectores_y_css() -> None:
    script = init_script({"profileGate": ".nuevo-selector", "errorView": ""})

    assert script.startswith("window.__fgConfig = ")
    assert ".nuevo-selector" in script
    assert "window.__fg = {" in script
    assert merged_selectors({"errorView": ""})["errorView"] == DEFAULT_SELECTORS["errorView"]


async def test_navegacion_abortada_no_es_un_error() -> None:
    page = FakePage([ready(10)])

    class Error(Exception):
        pass

    calls = {"n": 0}

    async def aborted_goto(url: str, **_: Any) -> None:
        calls["n"] += 1
        page.urls.append(url)
        raise Error("Page.goto: net::ERR_ABORTED at " + url)

    page.goto = aborted_goto  # type: ignore[method-assign]
    player, _ = make(page)

    assert await player.play(1, 10) == 10
    assert calls["n"] == 1
