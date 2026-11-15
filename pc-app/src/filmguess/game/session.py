"""Partida en curso: une catálogo, reproductor y marcador, sin depender de FastAPI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.catalog.random_pick import pick_random
from filmguess.catalog.search import SearchPage, search_movies
from filmguess.catalog.tmdb import poster_url
from filmguess.config import RandomConfig
from filmguess.game.scoreboard import Player, Scoreboard, ScoreboardError
from filmguess.player.base import Player as MoviePlayer
from filmguess.player.base import PlayerError, PlayerStatus, RevealInfo

log = logging.getLogger(__name__)
T = TypeVar("T")

Screen = Literal["espera", "cargando", "pelicula", "revelada"]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class GameError(Exception):
    """Orden que no se puede cumplir, con código de la API y mensaje en español."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código de error de la API.
            message: Mensaje para el usuario.
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Round:
    """La película que está en la tele."""

    movie: Movie
    seconds: int
    actual_seconds: float


@dataclass(slots=True)
class SessionState:
    """Foto del estado de la partida."""

    player: PlayerStatus
    screen: Screen
    round: Round | None
    last_command: str | None
    last_error: dict[str, str] | None
    players: list[Player] = field(default_factory=list)


async def _no_events(kind: str, data: dict[str, Any]) -> None:
    return None


class GameSession:
    """Estado de la partida y orquestación de las órdenes del móvil.

    Las órdenes al reproductor se ejecutan de una en una: solo hay una pestaña.
    """

    def __init__(
        self,
        db: CatalogDB,
        player: MoviePlayer,
        scoreboard: Scoreboard,
        random_config: RandomConfig,
        on_event: EventSink = _no_events,
    ) -> None:
        """Crea la sesión.

        Args:
            db: Catálogo.
            player: Reproductor de la tele.
            scoreboard: Marcador persistente.
            random_config: Umbrales de la película aleatoria.
            on_event: Recibe los eventos ``estado``, ``error`` y ``marcador``.
        """
        self.db = db
        self.player = player
        self.scoreboard = scoreboard
        self.random_config = random_config
        self.on_event = on_event
        self.screen: Screen = "espera"
        self.round: Round | None = None
        self.last_command: str | None = None
        self.last_error: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    # --- Consultas ---------------------------------------------------------------

    async def state(self) -> SessionState:
        """Estado actual.

        Returns:
            La foto del estado.
        """
        return SessionState(
            player=await self.player.status(),
            screen=self.screen,
            round=self.round,
            last_command=self.last_command,
            last_error=self.last_error,
            players=self.scoreboard.players,
        )

    def search(self, text: str, *, page: int = 1, include_all: bool = False) -> SearchPage:
        """Busca películas por título.

        Args:
            text: Texto buscado.
            page: Página (desde 1).
            include_all: Incluye las que no constan en Netflix España.

        Returns:
            La página de resultados.
        """
        return search_movies(self.db, text, page=page, include_unconfirmed=include_all)

    def random_movie(self) -> Movie:
        """Elige una película al azar entre las conocidas o bien valoradas.

        Returns:
            La película.

        Raises:
            GameError: Si no hay ninguna candidata (``sin_candidatas``).
        """
        movie = pick_random(self.db, self.random_config)
        if movie is None:
            raise GameError(
                "sin_candidatas",
                "No hay películas para elegir al azar. Importa el catálogo y las valoraciones.",
            )
        return movie

    def movie(self, netflix_id: int) -> Movie:
        """Ficha de una película.

        Args:
            netflix_id: ID de Netflix.

        Returns:
            La película.

        Raises:
            GameError: Si no está en el catálogo (``no_encontrada``).
        """
        movie = self.db.get(netflix_id)
        if movie is None:
            raise GameError("no_encontrada", "Esa película no está en el catálogo.")
        return movie

    # --- Órdenes a la tele -----------------------------------------------------------

    async def play(self, netflix_id: int, seconds: int) -> float:
        """Muestra la película pausada en el segundo pedido.

        Args:
            netflix_id: ID de Netflix.
            seconds: Segundo de la película.

        Returns:
            El segundo en el que quedó realmente.

        Raises:
            GameError: Si la película no existe, el segundo está fuera de la duración o el
                reproductor falla.
        """
        movie = self.movie(netflix_id)
        if seconds < 0 or (movie.runtime_s is not None and seconds > movie.runtime_s):
            limit = _hhmmss(movie.runtime_s or 0)
            raise GameError("fuera_de_duracion", f"Elige un momento entre 00:00:00 y {limit}.")
        # La tele pasa a «Preparando fotograma…» antes de que el navegador salga de /tv.
        self.screen = "cargando"
        await self._emit_state()
        try:
            actual = await self._run("play", self.player.play(netflix_id, seconds), movie)
        except GameError:
            # La pestaña ya no está en la película anterior: se vuelve a la espera.
            self.round = None
            self.screen = "espera"
            await self._back_to_idle()
            await self._emit_state()
            raise
        self.round = Round(movie, seconds, actual)
        self.screen = "pelicula"
        await self._emit_state()
        return actual

    async def pause(self) -> None:
        """Pausa la película de la ronda.

        Raises:
            GameError: Si no hay ronda o el reproductor falla.
        """
        self._require_round()
        await self._run("pause", self.player.pause())
        await self._emit_state()

    async def resume(self) -> None:
        """Reanuda la película de la ronda.

        Raises:
            GameError: Si no hay ronda o el reproductor falla.
        """
        self._require_round()
        await self._run("resume", self.player.resume())
        await self._emit_state()

    async def reveal(self) -> Movie:
        """Muestra el título y el póster de la película de la ronda.

        Returns:
            La película revelada.

        Raises:
            GameError: Si no hay ronda o el reproductor falla.
        """
        current = self._require_round()
        movie = current.movie
        original = movie.original_title
        if original and original.casefold() == movie.display_title.casefold():
            original = None
        info = RevealInfo(
            movie.display_title, movie.year, poster_url(movie.poster_path, "w780"), original
        )
        await self._run("reveal", self.player.reveal(info))
        self.screen = "revelada"
        await self._emit_state()
        return movie

    async def idle(self) -> None:
        """Termina la ronda y vuelve a la pantalla de espera.

        Raises:
            GameError: Si el reproductor falla.
        """
        await self._run("idle", self.player.idle())
        self.round = None
        self.screen = "espera"
        await self._emit_state()

    # --- Marcador --------------------------------------------------------------------

    async def add_player(self, name: str) -> Player:
        """Da de alta un jugador.

        Args:
            name: Nombre.

        Returns:
            El jugador.

        Raises:
            GameError: Si el nombre no es válido o ya existe.
        """
        player = self._scoreboard(lambda: self.scoreboard.add(name))
        await self._emit_scoreboard()
        return player

    async def remove_player(self, player_id: str) -> None:
        """Da de baja un jugador.

        Args:
            player_id: ID del jugador.

        Raises:
            GameError: Si no existe.
        """
        self._scoreboard(lambda: self.scoreboard.remove(player_id))
        await self._emit_scoreboard()

    async def add_points(self, player_id: str, points: int) -> Player:
        """Suma puntos a un jugador.

        Args:
            player_id: ID del jugador.
            points: Puntos (negativo para restar).

        Returns:
            El jugador actualizado.

        Raises:
            GameError: Si no existe.
        """
        player = self._scoreboard(lambda: self.scoreboard.add_points(player_id, points))
        await self._emit_scoreboard()
        return player

    async def reset_points(self) -> None:
        """Pone el marcador a cero."""
        self.scoreboard.reset_points()
        await self._emit_scoreboard()

    # --- Internos --------------------------------------------------------------------

    async def _run(self, command: str, action: Awaitable[T], movie: Movie | None = None) -> T:
        async with self._lock:
            self.last_command = f"{command} · {datetime.now(UTC).isoformat(timespec='seconds')}"
            try:
                result = await action
            except PlayerError as exc:
                self.last_error = {"error": exc.code, "mensaje": exc.message}
                if exc.code == "no_disponible_es" and movie is not None:
                    self.db.set_netflix_es(movie.netflix_id, False)
                log.warning("El reproductor falló en %s: %s (%s)", command, exc.message, exc.code)
                await self.on_event("error", dict(self.last_error))
                raise GameError(exc.code, exc.message) from exc
            self.last_error = None
            return result

    async def _back_to_idle(self) -> None:
        try:
            await self.player.idle()
        except PlayerError as exc:
            log.warning("No se pudo volver a la pantalla de espera: %s", exc.message)

    def _require_round(self) -> Round:
        if self.round is None:
            raise GameError("sin_ronda", "Primero elige una película y muéstrala en la tele.")
        return self.round

    def _scoreboard(self, action: Callable[[], T]) -> T:
        try:
            return action()
        except ScoreboardError as exc:
            raise GameError(exc.code, exc.message) from exc

    async def _emit_state(self) -> None:
        await self.on_event("estado", {"pantalla": self.screen})

    async def _emit_scoreboard(self) -> None:
        await self.on_event("marcador", {})


def _hhmmss(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
