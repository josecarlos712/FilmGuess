"""Selección de una película aleatoria entre las más conocidas y mejor valoradas."""

from __future__ import annotations

import random
from dataclasses import dataclass

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.config import RandomConfig

TMDB_VOTES_DIVISOR = 25
"""TMDB tiene muchos menos votos que IMDb: si se usa TMDB, el mínimo se divide por esto."""


@dataclass(frozen=True, slots=True)
class Rated:
    """Película con su nota ponderada."""

    movie: Movie
    weighted: float


def weighted_rating(rating: float, votes: int, min_votes: int, mean: float) -> float:
    """Nota ponderada bayesiana: ``v/(v+m)·R + m/(v+m)·C``.

    Args:
        rating: Nota media de la película (R).
        votes: Número de votos (v).
        min_votes: Votos de referencia (m).
        mean: Nota media del conjunto (C).

    Returns:
        La nota ponderada.
    """
    total = votes + min_votes
    if total <= 0:
        return mean
    return (votes / total) * rating + (min_votes / total) * mean


def rank_candidates(movies: list[Movie], min_votes: int) -> list[Rated]:
    """Ordena las candidatas por nota ponderada.

    Usa IMDb si alguna candidata tiene sus datos; si no, TMDB, con un mínimo de votos
    reducido.

    Args:
        movies: Candidatas (ya filtradas por año, disponibilidad y confianza).
        min_votes: Votos mínimos en IMDb.

    Returns:
        Las que superan el mínimo de votos, de mejor a peor.
    """
    use_imdb = any(m.imdb_votes for m in movies)
    threshold = min_votes if use_imdb else max(1, min_votes // TMDB_VOTES_DIVISOR)
    pairs: list[tuple[Movie, float, int]] = []
    for m in movies:
        rating = m.imdb_rating if use_imdb else m.tmdb_vote_avg
        votes = m.imdb_votes if use_imdb else m.tmdb_vote_count
        if rating is not None and votes is not None and votes >= threshold:
            pairs.append((m, rating, votes))
    if not pairs:
        return []
    mean = sum(r for _, r, _ in pairs) / len(pairs)
    ranked = [Rated(m, weighted_rating(r, v, threshold, mean)) for m, r, v in pairs]
    ranked.sort(key=lambda x: x.weighted, reverse=True)
    return ranked


def pick_random(
    db: CatalogDB, config: RandomConfig, rng: random.Random | None = None
) -> Movie | None:
    """Elige al azar una película del grupo de las mejor valoradas y la apunta.

    Evita las elegidas en las últimas ``config.no_repeat`` rondas, salvo que no quede
    ninguna otra en el grupo.

    Args:
        db: Catálogo.
        config: Umbrales del modo aleatorio.
        rng: Generador aleatorio (para pruebas).

    Returns:
        La película elegida, o ``None`` si no hay candidatas.
    """
    rng = rng or random.Random()
    ranked = rank_candidates(db.random_candidates(min_year=config.min_year), config.min_votes)
    pool = [r.movie for r in ranked[: config.pool_size]]
    if not pool:
        return None
    recent = set(db.recent_random(config.no_repeat))
    fresh = [m for m in pool if m.netflix_id not in recent]
    choice = rng.choice(fresh or pool)
    db.record_random(choice.netflix_id)
    return choice
