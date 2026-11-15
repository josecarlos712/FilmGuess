"""Modelos de datos del catálogo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["alta", "media", "baja", "sin_match"]


@dataclass(frozen=True, slots=True)
class TmdbMatch:
    """Datos de TMDB elegidos para una película del CSV."""

    tmdb_id: int
    title_es: str | None
    original_title: str | None
    year: int | None
    runtime_s: int | None
    poster_path: str | None
    imdb_id: str | None
    popularity: float | None
    vote_avg: float | None
    vote_count: int | None
    netflix_es: bool | None
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class Movie:
    """Una película del catálogo, tal como está guardada."""

    netflix_id: int
    csv_title: str
    title_es: str | None = None
    original_title: str | None = None
    year: int | None = None
    runtime_s: int | None = None
    poster_path: str | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    imdb_rating: float | None = None
    imdb_votes: int | None = None
    tmdb_popularity: float | None = None
    tmdb_vote_avg: float | None = None
    tmdb_vote_count: int | None = None
    netflix_es: bool | None = None
    match_conf: Confidence | None = None
    manual: bool = False

    @property
    def display_title(self) -> str:
        """Título que se muestra: en español si existe; si no, el original o el del CSV."""
        return self.title_es or self.original_title or self.csv_title
