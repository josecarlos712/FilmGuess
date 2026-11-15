"""Partida en curso: une catálogo, reproductor y marcador, sin depender de FastAPI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from filmguess.catalog.clues import Genre
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.catalog.random_pick import pick_random
from filmguess.catalog.search import SearchPage, search_movies
from filmguess.catalog.tmdb import poster_url
from filmguess.config import RandomConfig
from filmguess.game.hints import (
    CLIP_SECONDS,
    MAX_FRAMES,
    Hint,
    build_hints,
    random_moment,
    round_value,
)
from filmguess.game.scoreboard import Player, Scoreboard, ScoreboardError
from filmguess.player.base import Player as MoviePlayer
from filmguess.player.base import PlayerError, PlayerStatus, RevealInfo

log = logging.getLogger(__name__)
TEXT_SKIP = "Saltando escena…"
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


@dataclass(slots=True)
class Round:
    """La película que está en la tele y cómo va la ronda."""

    movie: Movie
    seconds: int
    actual_seconds: float
    category: Genre | None = None
    hints: list[Hint] = field(default_factory=list)
    revealed: int = 0
    frames: list[float] = field(default_factory=list)
    """Segundos de los fotogramas mostrados (el primero es el elegido)."""
    clip_used: bool = False
    awarded: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    """Jugadores que se han arriesgado y han fallado en esta ronda."""

    @property
    def value(self) -> int:
        """Puntos que vale acertar ahora."""
        return round_value(self.revealed, category=self.category is not None)

    @property
    def shown_hints(self) -> list[Hint]:
        """Pistas ya reveladas."""
        return self.hints[: self.revealed]


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
        self._clip: asyncio.Task[None] | None = None

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

    def search(
        self,
        text: str,
        *,
        page: int = 1,
        include_all: bool = False,
        genre_id: int | None = None,
    ) -> SearchPage:
        """Busca películas por título.

        Args:
            text: Texto buscado.
            page: Página (desde 1).
            include_all: Incluye las que no constan en Netflix España.
            genre_id: Solo películas de esta categoría.

        Returns:
            La página de resultados.
        """
        return search_movies(
            self.db, text, page=page, include_unconfirmed=include_all, genre_id=genre_id
        )

    def random_movie(self, genre_id: int | None = None) -> Movie:
        """Elige una película al azar entre las conocidas o bien valoradas.

        Args:
            genre_id: Solo películas de esta categoría.

        Returns:
            La película.

        Raises:
            GameError: Si no hay ninguna candidata (``sin_candidatas``).
        """
        movie = pick_random(self.db, self.random_config, genre_id=genre_id)
        if movie is None:
            message = (
                "No hay películas de esa categoría para elegir al azar."
                if genre_id is not None
                else "No hay películas para elegir al azar. Importa el catálogo y las valoraciones."
            )
            raise GameError("sin_candidatas", message)
        return movie

    def genres(self) -> list[tuple[Genre, int]]:
        """Categorías que se pueden jugar, con cuántas películas tienen.

        Returns:
            Pares (género, películas).
        """
        return self.db.genre_counts()

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

    async def play(self, netflix_id: int, seconds: int, genre_id: int | None = None) -> float:
        """Empieza una ronda: muestra la película pausada en el segundo pedido.

        Args:
            netflix_id: ID de Netflix.
            seconds: Segundo de la película.
            genre_id: Categoría elegida (la ronda vale como máximo 3 puntos).

        Returns:
            El segundo en el que quedó realmente.

        Raises:
            GameError: Si la película no existe, no es de la categoría, el segundo está fuera
                de la duración o el reproductor falla.
        """
        movie = self.movie(netflix_id)
        if seconds < 0 or (movie.runtime_s is not None and seconds > movie.runtime_s):
            limit = _hhmmss(movie.runtime_s or 0)
            raise GameError("fuera_de_duracion", f"Elige un momento entre 00:00:00 y {limit}.")
        clues = self.db.clues(netflix_id)
        category: Genre | None = None
        if genre_id is not None:
            category = next((g for g in (clues.genres if clues else []) if g.id == genre_id), None)
            if category is None:
                raise GameError(
                    "categoria_no_coincide", "Esa película no es de la categoría elegida."
                )
        self._stop_clip()
        # La tele pasa a «Preparando fotograma…» antes de que el navegador salga de /tv.
        self.screen = "cargando"
        await self._emit_state()
        actual = await self._show(movie, seconds)
        hints = build_hints(movie, clues, category=category is not None)
        self.round = Round(movie, seconds, actual, category, hints, frames=[actual])
        self.screen = "pelicula"
        await self._paint_hints()
        await self._emit_state()
        return actual

    async def reveal_hint(self) -> Hint:
        """Revela la siguiente pista (cuesta un punto, hasta un mínimo de 1).

        Returns:
            La pista revelada.

        Raises:
            GameError: Si no hay ronda en juego o no quedan pistas.
        """
        current = self._require_playing()
        if current.revealed >= len(current.hints):
            raise GameError("sin_pistas", "Ya se han dado todas las pistas.")
        current.revealed += 1
        await self._paint_hints()
        await self._emit_state()
        return current.hints[current.revealed - 1]

    async def next_frame(self) -> float:
        """Muestra otro fotograma al azar de la misma película (gratis, hasta 3 en total).

        Returns:
            El segundo en el que quedó.

        Raises:
            GameError: Si no hay ronda, ya se vieron los 3 fotogramas o el reproductor falla.
        """
        current = self._require_playing()
        if len(current.frames) >= MAX_FRAMES:
            raise GameError("sin_fotogramas", "Ya se han visto los tres fotogramas.")
        actual = await self._jump(current)
        current.frames.append(actual)
        await self._paint_hints()
        await self._emit_state()
        return actual

    async def skip_scene(self) -> float:
        """Tapa la tele al instante y cambia la escena por otra al azar (no gasta fotograma).

        Returns:
            El segundo en el que quedó.

        Raises:
            GameError: Si no hay ronda o el reproductor falla.
        """
        current = self._require_playing()
        self._stop_clip()
        try:
            await self.player.cover(TEXT_SKIP)
        except PlayerError as exc:
            log.warning("No se pudo tapar la escena: %s", exc.message)
        actual = await self._jump(current)
        current.frames[-1] = actual
        await self._paint_hints()
        await self._emit_state()
        return actual

    async def clip(self) -> None:
        """Reproduce 10 segundos desde el fotograma actual y vuelve a pausar (una vez).

        Raises:
            GameError: Si no hay ronda, el clip ya se usó o el reproductor falla.
        """
        current = self._require_playing()
        if current.clip_used:
            raise GameError("clip_usado", "El clip de 10 segundos ya se ha visto en esta ronda.")
        await self._run("clip", self.player.resume())
        current.clip_used = True
        self._clip = asyncio.create_task(self._end_clip(current))
        await self._emit_state()

    async def award(self, player_ids: list[str]) -> list[Player]:
        """Da los puntos que vale la ronda a quienes la han acertado (una vez por jugador).

        Args:
            player_ids: Jugadores que han acertado.

        Returns:
            Los jugadores que han recibido puntos.

        Raises:
            GameError: Si la película no se ha revelado aún o algún jugador no existe.
        """
        current = self._require_round()
        if self.screen != "revelada":
            raise GameError("sin_revelar", "Revela la película antes de repartir los puntos.")
        known = {p.id for p in self.scoreboard.players}
        missing = [pid for pid in player_ids if pid not in known]
        if missing:
            raise GameError("jugador_no_encontrado", "Ese jugador ya no está en el marcador.")
        given: list[Player] = []
        value = current.value
        for pid in dict.fromkeys(player_ids):
            if pid in current.awarded or pid in current.failed:
                continue  # ya los tiene, o falló y está fuera de la ronda
            given.append(self._give(pid, value))
            current.awarded.add(pid)
        await self._emit_scoreboard()
        await self._emit_state()
        return given

    def peek(self) -> Movie:
        """La película de la ronda, para comprobarla en el móvil sin enseñarla en la tele.

        Returns:
            La película en juego.

        Raises:
            GameError: Si no hay ronda.
        """
        return self._require_round().movie

    async def fail(self, player_ids: list[str]) -> list[Player]:
        """Resta a quien se arriesga y falla los puntos que vale la ronda en ese momento.

        La ronda sigue para los demás. Quien falla queda fuera: ya no puede recibir puntos
        en esta ronda.

        Args:
            player_ids: Jugadores que han fallado.

        Returns:
            Los jugadores que han perdido puntos.

        Raises:
            GameError: Si la película ya se ha revelado o algún jugador no existe.
        """
        current = self._require_round()
        if self.screen == "revelada":
            raise GameError("ronda_cerrada", "La película ya está revelada: nadie puede fallar.")
        known = {p.id for p in self.scoreboard.players}
        if any(pid not in known for pid in player_ids):
            raise GameError("jugador_no_encontrado", "Ese jugador ya no está en el marcador.")
        charged: list[Player] = []
        value = current.value
        for pid in dict.fromkeys(player_ids):
            if pid in current.failed:
                continue
            charged.append(self._give(pid, -value))
            current.failed.add(pid)
        await self._emit_scoreboard()
        await self._emit_state()
        return charged

    async def pause(self) -> None:
        """Pausa la película de la ronda.

        Raises:
            GameError: Si no hay ronda o el reproductor falla.
        """
        self._require_round()
        self._stop_clip()
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
        self._stop_clip()
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
        self._stop_clip()
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

    async def open_login(self) -> None:
        """Saca el navegador de la pantalla completa y abre el inicio de sesión de Netflix.

        Raises:
            GameError: Si el reproductor falla.
        """
        await self._run("login", self.player.open_login())
        self.round = None
        self.screen = "espera"
        await self._emit_state()

    async def rename_player(self, player_id: str, name: str) -> Player:
        """Cambia el nombre de un jugador.

        Args:
            player_id: ID del jugador.
            name: Nombre nuevo.

        Returns:
            El jugador actualizado.

        Raises:
            GameError: Si no existe o el nombre no es válido o ya existe.
        """
        player = self._scoreboard(lambda: self.scoreboard.rename(player_id, name))
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

    async def _show(self, movie: Movie, seconds: int) -> float:
        try:
            return await self._run("play", self.player.play(movie.netflix_id, seconds), movie)
        except GameError:
            # La pestaña ya no está en la película: se vuelve a la espera.
            self._stop_clip()
            self.round = None
            self.screen = "espera"
            await self._back_to_idle()
            await self._emit_state()
            raise

    async def _jump(self, current: Round) -> float:
        runtime = current.movie.runtime_s or int(max(current.frames) * 2) or 3600
        seconds = random_moment(runtime, current.frames)
        return await self._show(current.movie, seconds)

    async def _end_clip(self, current: Round) -> None:
        await asyncio.sleep(CLIP_SECONDS)
        if self.round is current and self.screen == "pelicula":
            try:
                await self._run("pause", self.player.pause())
            except GameError:
                return
            await self._emit_state()

    def _stop_clip(self) -> None:
        if self._clip is not None and not self._clip.done():
            self._clip.cancel()
        self._clip = None

    async def _paint_hints(self) -> None:
        current = self.round
        if current is None:
            return
        value = current.value
        data = {
            "valueText": f"Vale {value} punto{'s' if value != 1 else ''}",
            "category": current.category.name if current.category else None,
            "frameText": f"Fotograma {len(current.frames)} de {MAX_FRAMES}",
            "hints": [
                {"label": h.label, "text": h.text, "long": h.long} for h in current.shown_hints
            ],
        }
        try:
            await self.player.show_hints(data)
        except PlayerError as exc:
            log.warning("No se pudieron pintar las pistas: %s", exc.message)

    def _require_playing(self) -> Round:
        current = self._require_round()
        if self.screen != "pelicula":
            raise GameError("ronda_cerrada", "La película ya se ha revelado: empieza otra ronda.")
        return current

    async def _back_to_idle(self) -> None:
        try:
            await self.player.idle()
        except PlayerError as exc:
            log.warning("No se pudo volver a la pantalla de espera: %s", exc.message)

    def _require_round(self) -> Round:
        if self.round is None:
            raise GameError("sin_ronda", "Primero elige una película y muéstrala en la tele.")
        return self.round

    def _give(self, player_id: str, points: int) -> Player:
        return self._scoreboard(lambda: self.scoreboard.add_points(player_id, points))

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
