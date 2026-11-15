"""Utilidades comunes de los tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Confidence, TmdbMatch


@pytest.fixture
def db() -> Iterator[CatalogDB]:
    catalog = CatalogDB(":memory:")
    yield catalog
    catalog.close()


def make_match(
    tmdb_id: int = 1,
    *,
    title_es: str = "Origen",
    original_title: str = "Inception",
    year: int | None = 2010,
    netflix_es: bool | None = True,
    confidence: Confidence = "alta",
    imdb_id: str | None = None,
    vote_avg: float | None = 8.0,
    vote_count: int | None = 30000,
) -> TmdbMatch:
    return TmdbMatch(
        tmdb_id=tmdb_id,
        title_es=title_es,
        original_title=original_title,
        year=year,
        runtime_s=8880,
        poster_path="/p.jpg",
        imdb_id=imdb_id,
        popularity=50.0,
        vote_avg=vote_avg,
        vote_count=vote_count,
        netflix_es=netflix_es,
        confidence=confidence,
    )


def details_json(
    tmdb_id: int,
    title: str,
    original_title: str,
    *,
    year: int = 2010,
    netflix_es: bool = False,
    popularity: float = 10.0,
    imdb_id: str = "tt0000001",
) -> dict[str, Any]:
    flatrate = [{"provider_id": 8, "provider_name": "Netflix"}] if netflix_es else []
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original_title,
        "release_date": f"{year}-07-16",
        "runtime": 148,
        "poster_path": f"/{tmdb_id}.jpg",
        "popularity": popularity,
        "vote_average": 8.4,
        "vote_count": 35000,
        "external_ids": {"imdb_id": imdb_id},
        "watch/providers": {"results": {"ES": {"flatrate": flatrate}} if flatrate else {}},
    }
