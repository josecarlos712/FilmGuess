"""Reproductor de Netflix: abre la película, la deja pausada en un segundo y la revela."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from filmguess.player.base import PlayerError, PlayerStatus, RevealInfo

log = logging.getLogger(__name__)

WATCH_URL = "https://www.netflix.com/watch/{id}?t={seconds}"
LOGIN_URL = "https://www.netflix.com/login"
TOLERANCE_S = 2.0
POLL_S = 0.15
MAX_REDIRECTS = 2
ERROR_TEXT_MAX = 120
CALL_JS = """([name, arg]) => {
  if (!window.__fg) return { __missing: true };
  return window.__fg[name](arg);
}"""
"""Llama a una función de ``netflix_api.js``; si aún no existe, lo dice."""


class PageLike(Protocol):
    """Lo que el reproductor usa de una pestaña de Playwright."""

    @property
    def url(self) -> str:
        """URL actual."""
        ...

    async def goto(self, url: str, *, wait_until: Any = ..., timeout: float | None = ...) -> Any:
        """Navega a una URL."""
        ...

    async def evaluate(self, expression: str, arg: Any = ...) -> Any:
        """Ejecuta JavaScript en la página."""
        ...


class BrowserLike(Protocol):
    """Lo que el reproductor usa del navegador."""

    def is_open(self) -> bool:
        """Si el navegador está abierto."""
        ...

    async def start(self) -> None:
        """Abre el navegador."""
        ...

    async def page(self) -> PageLike:
        """La pestaña de la tele."""
        ...

    async def fullscreen(self) -> None:
        """Pantalla completa."""
        ...

    async def leave_fullscreen(self) -> None:
        """Ventana normal."""
        ...

    async def close(self) -> None:
        """Cierra el navegador."""
        ...


class NetflixPlayer:
    """Implementación del reproductor sobre Netflix en el navegador de la tele."""

    def __init__(
        self,
        browser: BrowserLike,
        *,
        idle_url: str,
        profile_name: str = "",
        timeout_s: float = 15,
        clock: Any = time.monotonic,
        sleep: Any = asyncio.sleep,
    ) -> None:
        """Crea el reproductor.

        Args:
            browser: Navegador de la tele.
            idle_url: Pantalla de espera (``/tv`` del propio servidor).
            profile_name: Perfil de Netflix que se elige si Netflix lo pide.
            timeout_s: Tiempo máximo para dejar el fotograma listo.
            clock: Reloj monótono (para pruebas).
            sleep: Espera asíncrona (para pruebas).
        """
        self.browser = browser
        self.idle_url = idle_url
        self.profile_name = profile_name
        self.timeout_s = timeout_s
        self._clock = clock
        self._sleep = sleep
        self._session: bool | None = None

    # --- Ciclo de vida -------------------------------------------------------------

    async def start(self) -> None:
        """Abre el navegador (la pantalla de espera se carga con :meth:`idle`).

        Raises:
            PlayerError: Si el navegador no se puede abrir.
        """
        await self.browser.start()

    async def close(self) -> None:
        """Cierra el navegador."""
        await self.browser.close()

    async def status(self) -> PlayerStatus:
        """Estado del navegador y de la sesión de Netflix (la última conocida).

        Returns:
            El estado.
        """
        return PlayerStatus(browser=self.browser.is_open(), netflix_session=self._session)

    # --- Órdenes ---------------------------------------------------------------------

    async def play(self, netflix_id: int, seconds: int) -> float:
        """Abre la película y la deja pausada en ``seconds`` (±2 s), a pantalla completa.

        Args:
            netflix_id: ID de Netflix.
            seconds: Segundo pedido.

        Returns:
            El segundo en el que ha quedado.

        Raises:
            PlayerError: Con ``sesion_caducada``, ``perfil_requerido``, ``no_disponible_es``,
                ``netflix_error``, ``tiempo_agotado`` o ``navegador_cerrado``.
        """
        started = self._clock()
        deadline = started + self.timeout_s
        page = await self.browser.page()
        url = WATCH_URL.format(id=netflix_id, seconds=seconds)
        if f"/watch/{netflix_id}" in page.url and await self._call_soft(page, "cover"):
            # Misma película (otro fotograma): se tapa y se salta sin recargar la página.
            await self._call_soft(page, "hideHints")
        else:
            await self._goto(page, url)
        state = await self._wait_ready(page, url, deadline)
        current = await self._settle_position(page, url, seconds, state, deadline)
        await self._call(page, "pause")
        await self.browser.fullscreen()
        await self._call(page, "uncover")  # el fotograma ya está quieto: se quita la cubierta
        log.info(
            "Netflix %d listo en %.1f s (pedido %d s, real %.1f s)",
            netflix_id,
            self._clock() - started,
            seconds,
            current,
        )
        return current

    async def pause(self) -> None:
        """Pausa la película.

        Raises:
            PlayerError: Si el navegador está cerrado.
        """
        await self._call(await self.browser.page(), "pause")

    async def resume(self) -> None:
        """Reanuda la película (y quita la capa de revelar si estaba).

        Raises:
            PlayerError: Si el navegador está cerrado.
        """
        await self._call(await self.browser.page(), "play")

    async def cover(self, message: str | None = None) -> None:
        """Tapa la tele al instante (por ejemplo, para saltar una escena).

        Args:
            message: Texto de la cubierta; por defecto, «Preparando fotograma…».

        Raises:
            PlayerError: Si el navegador está cerrado o la pestaña no es Netflix.
        """
        page = await self.browser.page()
        await self._call(page, "cover", message)
        await self._call(page, "pause")

    async def show_hints(self, data: dict[str, Any]) -> None:
        """Pinta en una esquina el valor de la ronda, el fotograma y las pistas reveladas.

        Args:
            data: ``valueText``, ``category``, ``frameText`` y ``hints`` (``label``, ``text``,
                ``long``).

        Raises:
            PlayerError: Si el navegador está cerrado o la pestaña no es Netflix.
        """
        await self._call(await self.browser.page(), "showHints", data)

    async def reveal(self, info: RevealInfo) -> None:
        """Muestra el título y el póster sobre el vídeo pausado.

        Args:
            info: Título, año y póster.

        Raises:
            PlayerError: Si el navegador está cerrado o la pestaña no es Netflix.
        """
        page = await self.browser.page()
        await self._call(page, "pause")
        data = {
            "title": info.title,
            "year": info.year,
            "poster": info.poster_url,
            "original": info.original_title,
        }
        await self._call(page, "showReveal", data)

    async def idle(self) -> None:
        """Vuelve a la pantalla de espera.

        Raises:
            PlayerError: Si el navegador está cerrado.
        """
        page = await self.browser.page()
        await self._goto(page, self.idle_url)
        await self.browser.fullscreen()

    async def open_login(self) -> None:
        """Abre el inicio de sesión de Netflix en una ventana normal.

        Raises:
            PlayerError: Si el navegador está cerrado.
        """
        page = await self.browser.page()
        await self.browser.leave_fullscreen()
        await self._goto(page, LOGIN_URL)
        self._session = None

    # --- Internos --------------------------------------------------------------------

    async def _goto(self, page: PageLike, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
        except Exception as exc:  # Playwright: TimeoutError, Error
            if type(exc).__name__ == "TimeoutError":
                raise PlayerError(
                    "tiempo_agotado", "La página tarda demasiado en cargar. ¿Hay conexión?"
                ) from exc
            if "ERR_ABORTED" in str(exc) and self.browser.is_open():
                # Netflix sustituye la navegación por la suya (redirecciones del reproductor):
                # el bucle de espera decide por la pantalla en la que acabe.
                log.info("Navegación a %s sustituida por la página: se sigue esperando", url)
                return
            raise self._as_player_error(exc) from exc

    async def _call(self, page: PageLike, name: str, arg: Any = None) -> Any:
        try:
            result = await page.evaluate(CALL_JS, [name, arg])
        except Exception as exc:  # Playwright: Error (pestaña cerrada, navegación...)
            raise self._as_player_error(exc) from exc
        if isinstance(result, dict) and result.get("__missing"):
            raise PlayerError(
                "netflix_error", "La pestaña de la tele no está en Netflix. Prueba otra vez."
            )
        return result

    async def _state(self, page: PageLike) -> dict[str, Any]:
        result = await self._call_soft(page, "state")
        return result if isinstance(result, dict) else {"kind": "loading"}

    async def _call_soft(self, page: PageLike, name: str, arg: Any = None) -> Any:
        try:
            return await self._call(page, name, arg)
        except PlayerError as exc:
            if exc.code == "navegador_cerrado":
                raise
            return None  # la página está cargando o navegando: se reintenta

    async def _wait_ready(self, page: PageLike, url: str, deadline: float) -> dict[str, Any]:
        redirects = 0
        chose_profile = False
        retried_error = False
        while True:
            state = await self._state(page)
            kind = state.get("kind")
            if kind == "ready":
                self._session = True
                return state
            if kind == "login":
                self._session = False
                raise PlayerError(
                    "sesion_caducada",
                    "Netflix ha cerrado la sesión. Inicia sesión en la ventana del PC.",
                )
            if kind == "pin":
                raise PlayerError(
                    "perfil_requerido", "El perfil de Netflix tiene PIN. Escríbelo en el PC."
                )
            if kind == "profiles":
                self._session = True
                if chose_profile or not await self._choose_profile(page, state):
                    names = ", ".join(state.get("profiles") or []) or "ninguno"
                    raise PlayerError(
                        "perfil_requerido",
                        "Netflix pide elegir perfil. Pon el nombre en [netflix] profile_name "
                        f"de config.toml (perfiles: {names}).",
                    )
                chose_profile = True
            elif kind == "continue":
                await self._call_soft(page, "clickContinue")
            elif kind == "unavailable":
                raise PlayerError(
                    "no_disponible_es", "Netflix dice que esta película no está disponible aquí."
                )
            elif kind == "error":
                code = state.get("code") or "sin código"
                message = " ".join(str(state.get("message") or "").split())
                log.warning(
                    "Netflix muestra un error (%s) en %s: %s",
                    code,
                    page.url,
                    message or "(sin texto)",
                )
                if not retried_error:
                    # Muchos errores de Netflix son pasajeros: se recarga una vez.
                    retried_error = True
                    await self._goto(page, url)
                else:
                    detail = f": «{message[:ERROR_TEXT_MAX]}»" if message else "."
                    raise PlayerError("netflix_error", f"Netflix muestra un error ({code}){detail}")
            elif kind == "elsewhere":
                # Tras elegir perfil, Netflix a veces lleva a la portada: se vuelve a pedir.
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise PlayerError(
                        "netflix_error", "Netflix no abre la película; sigue en otra página."
                    )
                await self._goto(page, url)
            if self._clock() >= deadline:
                raise PlayerError(
                    "tiempo_agotado", "Netflix tarda demasiado en cargar la película."
                )
            await self._sleep(POLL_S)

    async def _choose_profile(self, page: PageLike, state: dict[str, Any]) -> bool:
        if not self.profile_name:
            return False
        log.info("Netflix pide perfil; se elige «%s»", self.profile_name)
        return bool(await self._call_soft(page, "chooseProfile", self.profile_name))

    async def _settle_position(
        self, page: PageLike, url: str, seconds: int, state: dict[str, Any], deadline: float
    ) -> float:
        current = _as_float(state.get("currentTime"))
        if current is not None and abs(current - seconds) <= TOLERANCE_S:
            return current
        if state.get("hasApi") and await self._call_soft(page, "seek", seconds):
            current = await self._wait_position(page, seconds, deadline)
            if current is not None:
                return current
        log.info("El salto con la API no llegó; se recarga con ?t=%d", seconds)
        await self._goto(page, url)
        state = await self._wait_ready(page, url, deadline)
        current = await self._wait_position(page, seconds, deadline)
        if current is None:
            got = _as_float(state.get("currentTime"))
            raise PlayerError(
                "tiempo_agotado",
                f"Netflix no se colocó en el segundo pedido (está en {got or 0:.0f} s).",
            )
        return current

    async def _wait_position(self, page: PageLike, seconds: int, deadline: float) -> float | None:
        while self._clock() < deadline:
            state = await self._state(page)
            current = _as_float(state.get("currentTime"))
            ready = state.get("kind") == "ready" and current is not None
            if ready and current is not None and abs(current - seconds) <= TOLERANCE_S:
                return current
            await self._sleep(POLL_S)
        return None

    def _as_player_error(self, exc: Exception) -> PlayerError:
        message = str(exc)
        if not self.browser.is_open() or "closed" in message.lower():
            return PlayerError("navegador_cerrado", "Se ha cerrado el navegador de la tele.")
        return PlayerError("netflix_error", f"Fallo al controlar Netflix: {message[:200]}")


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
