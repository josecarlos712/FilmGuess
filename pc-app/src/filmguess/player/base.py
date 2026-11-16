"""Interfaz del reproductor que controla la pestaña de la tele.

El servidor solo depende de esta interfaz; la implementación con Netflix llega en F3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PlayerError(Exception):
    """Fallo del reproductor con un código de la API y un mensaje en español."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código de error (``navegador_cerrado``, ``no_disponible_es``...).
            message: Mensaje para el usuario.
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PlayerStatus:
    """Estado del navegador."""

    browser: bool
    """Si el navegador está abierto."""
    netflix_session: bool | None
    """Si hay sesión iniciada en Netflix (``None`` si aún no se sabe)."""


@dataclass(frozen=True, slots=True)
class RevealInfo:
    """Lo que se muestra al revelar la película."""

    title: str
    year: int | None
    poster_url: str | None
    original_title: str | None = None
    """Título original, solo si es distinto del que se muestra."""


class Player(Protocol):
    """Operaciones del reproductor (todas asíncronas; el servidor no las solapa)."""

    async def start(self) -> None:
        """Prepara el reproductor al arrancar el servidor (abre el navegador)."""
        ...

    async def close(self) -> None:
        """Libera el reproductor al parar el servidor."""
        ...

    async def status(self) -> PlayerStatus:
        """Estado actual del navegador."""
        ...

    async def play(self, netflix_id: int, seconds: int) -> float:
        """Abre la película, la deja pausada en ``seconds`` y devuelve el segundo real."""
        ...

    async def pause(self) -> None:
        """Pausa la reproducción."""
        ...

    async def resume(self) -> None:
        """Reanuda la reproducción."""
        ...

    async def cover(self, message: str | None = None) -> None:
        """Tapa la tele al instante y pausa (para saltar una escena)."""
        ...

    async def show_hints(self, data: dict[str, Any]) -> None:
        """Pinta en una esquina el valor de la ronda y las pistas reveladas."""
        ...

    async def reveal(self, info: RevealInfo) -> None:
        """Muestra el título y el póster sobre el vídeo pausado."""
        ...

    async def notify(self, data: dict[str, Any]) -> None:
        """Aviso breve en una esquina (acierto, fallo, corrección); no tapa la escena."""
        ...

    async def show_scores(self, data: dict[str, Any]) -> None:
        """Marcador de fin de ronda junto a la película revelada."""
        ...

    async def idle(self) -> None:
        """Vuelve a la pantalla de espera."""
        ...

    async def open_login(self) -> None:
        """Abre el inicio de sesión de Netflix en una ventana normal (sin pantalla completa)."""
        ...


class UnavailablePlayer:
    """Reproductor de reserva mientras no hay navegador: todas las órdenes fallan con claridad."""

    async def start(self) -> None:
        """No hay nada que abrir."""

    async def close(self) -> None:
        """No hay nada que cerrar."""

    async def status(self) -> PlayerStatus:
        """Sin navegador.

        Returns:
            Estado con el navegador cerrado.
        """
        return PlayerStatus(browser=False, netflix_session=None)

    async def play(self, netflix_id: int, seconds: int) -> float:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def pause(self) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def resume(self) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def cover(self, message: str | None = None) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def show_hints(self, data: dict[str, Any]) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def reveal(self, info: RevealInfo) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    async def notify(self, data: dict[str, Any]) -> None:
        """Sin navegador no hay dónde avisar: los avisos son decorativos."""

    async def show_scores(self, data: dict[str, Any]) -> None:
        """Sin navegador no hay dónde pintar el marcador."""

    async def idle(self) -> None:
        """No hay nada que hacer sin navegador."""

    async def open_login(self) -> None:
        """Siempre falla.

        Raises:
            PlayerError: Siempre, con ``navegador_cerrado``.
        """
        raise self._error()

    @staticmethod
    def _error() -> PlayerError:
        return PlayerError("navegador_cerrado", "El navegador de la tele no está abierto en el PC.")
