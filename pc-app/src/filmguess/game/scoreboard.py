"""Marcador de jugadores, guardado en JSON para que sobreviva a los reinicios."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_NAME = 30
MAX_PLAYERS = 12


class ScoreboardError(Exception):
    """Operación no válida sobre el marcador (el mensaje se puede mostrar al usuario)."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código de error de la API.
            message: Mensaje en español.
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class Player:
    """Un jugador y sus puntos."""

    id: str
    name: str
    points: int = 0


class Scoreboard:
    """Jugadores y puntos; cada cambio se guarda en disco al momento."""

    def __init__(self, path: Path | None) -> None:
        """Carga el marcador.

        Args:
            path: Fichero JSON; ``None`` para un marcador solo en memoria (pruebas).

        Raises:
            ScoreboardError: Si el fichero existe pero no se puede leer.
        """
        self._path = path
        self._players: list[Player] = []
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._players = [
                    Player(str(p["id"]), str(p["name"]), int(p["points"])) for p in raw
                ]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ScoreboardError("marcador_danado", f"No se pudo leer {path}: {exc}") from exc

    @property
    def players(self) -> list[Player]:
        """Jugadores en orden de alta."""
        return list(self._players)

    def add(self, name: str) -> Player:
        """Da de alta un jugador.

        Args:
            name: Nombre (se recortan los espacios).

        Returns:
            El jugador creado.

        Raises:
            ScoreboardError: Si el nombre está vacío, es demasiado largo o ya existe, o si
                ya hay demasiados jugadores.
        """
        clean = " ".join(name.split())
        if not clean:
            raise ScoreboardError("nombre_invalido", "Escribe un nombre para el jugador.")
        if len(clean) > MAX_NAME:
            raise ScoreboardError("nombre_invalido", f"El nombre admite {MAX_NAME} caracteres.")
        if any(p.name.casefold() == clean.casefold() for p in self._players):
            raise ScoreboardError("nombre_repetido", f"Ya hay un jugador llamado «{clean}».")
        if len(self._players) >= MAX_PLAYERS:
            raise ScoreboardError("demasiados_jugadores", f"Máximo {MAX_PLAYERS} jugadores.")
        player = Player(secrets.token_hex(4), clean)
        self._players.append(player)
        self._save()
        return player

    def remove(self, player_id: str) -> None:
        """Da de baja un jugador.

        Args:
            player_id: ID del jugador.

        Raises:
            ScoreboardError: Si no existe.
        """
        self._players.remove(self._find(player_id))
        self._save()

    def add_points(self, player_id: str, points: int) -> Player:
        """Suma (o resta, si es negativo) puntos a un jugador.

        Args:
            player_id: ID del jugador.
            points: Puntos a sumar.

        Returns:
            El jugador actualizado.

        Raises:
            ScoreboardError: Si no existe.
        """
        player = self._find(player_id)
        player.points += points
        self._save()
        return player

    def reset_points(self) -> None:
        """Pone a cero los puntos de todos."""
        for player in self._players:
            player.points = 0
        self._save()

    def _find(self, player_id: str) -> Player:
        for player in self._players:
            if player.id == player_id:
                return player
        raise ScoreboardError("jugador_no_encontrado", "Ese jugador ya no está en el marcador.")

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = [asdict(p) for p in self._players]
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
