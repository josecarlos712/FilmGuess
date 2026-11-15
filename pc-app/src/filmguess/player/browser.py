"""Navegador de la tele: Edge (o Chrome) con perfil persistente y una sola pestaña."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from filmguess.config import BrowserConfig
from filmguess.player.base import PlayerError

log = logging.getLogger(__name__)

IGNORED_DEFAULT_ARGS = ["--enable-automation", "--disable-component-update"]
"""Sin la barra «controlado por software de prueba» y dejando actualizar el módulo DRM."""


class Browser:
    """Ciclo de vida del navegador controlado con Playwright.

    El perfil se guarda en disco, así que la sesión de Netflix sobrevive a los reinicios.
    """

    def __init__(
        self, config: BrowserConfig, profile_dir: Path, init_js: str, *, headless: bool = False
    ) -> None:
        """Prepara el navegador (no lo abre).

        Args:
            config: Canal, GPU y pantalla completa.
            profile_dir: Carpeta del perfil persistente.
            init_js: Script que se ejecuta en cada página antes que el de la web.
            headless: Sin ventana (solo para pruebas; Netflix necesita ventana).
        """
        self.config = config
        self.profile_dir = profile_dir
        self.init_js = init_js
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def is_open(self) -> bool:
        """Indica si el navegador y su pestaña siguen abiertos.

        Returns:
            ``True`` si se puede usar.
        """
        return self._page is not None and not self._page.is_closed()

    def launch_args(self) -> list[str]:
        """Argumentos de línea de comandos del navegador.

        Returns:
            Lista de argumentos.
        """
        args = ["--autoplay-policy=no-user-gesture-required", "--no-first-run"]
        if self.config.disable_gpu:
            args.append("--disable-gpu")
        return args

    async def start(self) -> None:
        """Abre el navegador si no lo está.

        Raises:
            PlayerError: Si no se puede abrir (``navegador_cerrado``).
        """
        if self.is_open():
            return
        await self.close()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = await async_playwright().start()
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel=self.config.channel,
                headless=self.headless,
                no_viewport=True,
                bypass_csp=True,
                args=self.launch_args(),
                ignore_default_args=IGNORED_DEFAULT_ARGS,
            )
            if self.init_js:
                await self._context.add_init_script(self.init_js)
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            for extra in pages[1:]:
                await extra.close()
            self._context.on("close", lambda _: self._forget())
        except PlaywrightError as exc:
            await self.close()
            name = "Microsoft Edge" if self.config.channel == "msedge" else "Google Chrome"
            raise PlayerError(
                "navegador_cerrado",
                f"No se pudo abrir {name}. ¿Está instalado y sin otra ventana usando el perfil? "
                f"({exc.message.splitlines()[0]})",
            ) from exc
        log.info("Navegador abierto (%s) con el perfil %s", self.config.channel, self.profile_dir)

    async def page(self) -> Page:
        """La pestaña de la tele, abriendo el navegador si hace falta.

        Returns:
            La pestaña.

        Raises:
            PlayerError: Si no se puede abrir.
        """
        await self.start()
        assert self._page is not None
        return self._page

    async def leave_fullscreen(self) -> None:
        """Deja la ventana en tamaño normal (para iniciar sesión a mano)."""
        page = await self.page()
        try:
            cdp = await page.context.new_cdp_session(page)
            window: dict[str, Any] = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
            )
            await cdp.detach()
        except PlaywrightError as exc:
            log.warning("No se pudo quitar la pantalla completa: %s", exc.message)

    async def fullscreen(self) -> None:
        """Pone la ventana a pantalla completa (por CDP, porque F11 no llega a Playwright).

        Raises:
            PlayerError: Si el navegador está cerrado.
        """
        if not self.config.fullscreen:
            return
        page = await self.page()
        try:
            cdp = await page.context.new_cdp_session(page)
            window: dict[str, Any] = await cdp.send("Browser.getWindowForTarget")
            bounds = window.get("bounds", {})
            if bounds.get("windowState") != "fullscreen":
                await cdp.send(
                    "Browser.setWindowBounds",
                    {"windowId": window["windowId"], "bounds": {"windowState": "fullscreen"}},
                )
            await cdp.detach()
        except PlaywrightError as exc:
            log.warning("No se pudo poner la pantalla completa: %s", exc.message)

    async def close(self) -> None:
        """Cierra el navegador y Playwright (sin errores si ya estaban cerrados)."""
        if self._context is not None:
            with contextlib.suppress(PlaywrightError):
                await self._context.close()
        if self._playwright is not None:
            with contextlib.suppress(PlaywrightError):
                await self._playwright.stop()
        self._forget()
        self._playwright = None

    def _forget(self) -> None:
        self._context = None
        self._page = None


async def open_for_login(config: BrowserConfig, profile_dir: Path, url: str) -> None:
    """Abre el perfil de la app en una ventana normal y espera a que el usuario la cierre.

    Sirve para iniciar sesión en Netflix la primera vez (la sesión queda en el perfil).

    Args:
        config: Configuración del navegador (se ignora la pantalla completa).
        profile_dir: Carpeta del perfil persistente.
        url: Página que se abre.

    Raises:
        PlayerError: Si el navegador no se puede abrir.
    """
    windowed = config.model_copy(update={"fullscreen": False})
    browser = Browser(windowed, profile_dir, "")
    page = await browser.page()
    closed = asyncio.Event()
    page.on("close", lambda _: closed.set())
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        log.warning("No se pudo abrir %s: %s", url, exc.message)
    await closed.wait()
    await browser.close()
