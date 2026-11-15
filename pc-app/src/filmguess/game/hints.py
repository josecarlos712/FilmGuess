"""Reglas de la ronda: pistas, valor en puntos y momentos para los otros fotogramas.

Sin categoría la ronda vale 5 puntos y hay cinco pistas: género, año y nota de IMDb,
actor o actriz principal, su personaje y una frase (o crítica). Con categoría el género se
sabe desde el principio, la ronda vale 3 y hay tres pistas: año y nota, actor y personaje
juntos, y la frase. Cada pista resta un punto, pero acertar vale siempre al menos 1.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from filmguess.catalog.clues import Clues, Genre, is_mostly_masked
from filmguess.catalog.models import Movie

HintKind = Literal["genero", "anio_nota", "actor", "personaje", "reparto", "frase"]

POINTS_FREE = 5
POINTS_CATEGORY = 3
MIN_POINTS = 1
MAX_FRAMES = 3
CLIP_SECONDS = 5
MOMENT_MARGIN = 0.05
"""Los fotogramas al azar evitan el 5 % del principio y el 10 % del final (créditos)."""
MOMENT_TAIL = 0.10
MOMENT_GAP = 0.10
"""Distancia mínima entre fotogramas, como fracción de la duración."""
CHARACTER_IN_TITLE = "Su nombre está en el título"


@dataclass(frozen=True, slots=True)
class Hint:
    """Una pista de la ronda."""

    kind: HintKind
    label: str
    text: str

    @property
    def long(self) -> bool:
        """Si es un texto largo (frase o crítica)."""
        return self.kind == "frase"


def _year_rating(movie: Movie) -> str | None:
    parts = []
    if movie.year:
        parts.append(str(movie.year))
    if movie.imdb_rating is not None:
        parts.append(f"IMDb {movie.imdb_rating:.1f}".replace(".", ","))
    return " · ".join(parts) or None


def _character(clues: Clues) -> str | None:
    if not clues.character:
        return None
    return CHARACTER_IN_TITLE if is_mostly_masked(clues.character) else clues.character


def _quote_label(clues: Clues) -> str:
    if clues.quote_source == "critica":
        label = "Una crítica"
    elif clues.quote_source == "eslogan":
        label = "El eslogan"
    else:
        label = "Una frase de la película"
    return f"{label} (en inglés)" if clues.quote_lang == "en" else label


def genres_text(genres: Sequence[Genre]) -> str:
    """Géneros para mostrar.

    Args:
        genres: Géneros de la película.

    Returns:
        Nombres separados por « · ».
    """
    return " · ".join(g.name for g in genres)


def build_hints(movie: Movie, clues: Clues | None, *, category: bool) -> list[Hint]:
    """Pistas de una película en el orden en que se revelan.

    Las que no tienen datos se omiten.

    Args:
        movie: Película de la ronda.
        clues: Sus pistas descargadas (``None`` si aún no hay).
        category: Si la ronda se juega con categoría (el género ya se sabe).

    Returns:
        Las pistas.
    """
    clues = clues or Clues()
    hints: list[Hint] = []
    if not category and clues.genres:
        hints.append(Hint("genero", "Género", genres_text(clues.genres)))
    year_rating = _year_rating(movie)
    if year_rating:
        hints.append(Hint("anio_nota", "Año y nota", year_rating))
    character = _character(clues)
    if category:
        if clues.actor and character:
            hints.append(Hint("reparto", "Protagonista", f"{clues.actor}, como {character}"))
        elif clues.actor or character:
            hints.append(Hint("reparto", "Protagonista", str(clues.actor or character)))
    else:
        if clues.actor:
            hints.append(Hint("actor", "Protagonista", clues.actor))
        if character:
            hints.append(Hint("personaje", "Su personaje", character))
    if clues.quote:
        hints.append(Hint("frase", _quote_label(clues), clues.quote))
    return hints


def round_value(revealed: int, *, category: bool) -> int:
    """Puntos que vale acertar tras revelar ``revealed`` pistas.

    Args:
        revealed: Pistas reveladas.
        category: Si la ronda se juega con categoría.

    Returns:
        De 5 (o 3 con categoría) a 1.
    """
    base = POINTS_CATEGORY if category else POINTS_FREE
    return max(MIN_POINTS, base - max(0, revealed))


def random_moment(runtime_s: int, shown: Sequence[float], rng: random.Random | None = None) -> int:
    """Segundo al azar lejos de los fotogramas ya vistos y de los créditos.

    Args:
        runtime_s: Duración de la película.
        shown: Segundos ya mostrados en la ronda.
        rng: Generador aleatorio (para pruebas).

    Returns:
        El segundo elegido.
    """
    rng = rng or random.Random()
    low = int(runtime_s * MOMENT_MARGIN)
    high = max(low + 1, int(runtime_s * (1 - MOMENT_TAIL)))
    gap = runtime_s * MOMENT_GAP
    best, best_distance = low, -1.0
    for _ in range(60):
        candidate = rng.randint(low, high)
        distance = min((abs(candidate - s) for s in shown), default=float("inf"))
        if distance >= gap:
            return candidate
        if distance > best_distance:
            best, best_distance = candidate, distance
    return best
