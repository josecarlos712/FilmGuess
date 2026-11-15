"""Tests del emparejamiento CSV ↔ TMDB."""

from __future__ import annotations

from typing import Any

from filmguess.catalog.matching import (
    choose_match,
    confidence_for,
    rank_by_similarity,
)
from filmguess.catalog.tmdb import TmdbCandidate
from tests.conftest import details_json


def cand(tmdb_id: int, title: str, original: str | None = None, pop: float = 1.0) -> TmdbCandidate:
    return TmdbCandidate(tmdb_id, title, original or title, 2000, pop)


class FakeDetails:
    def __init__(self, data: dict[int, dict[str, Any]]) -> None:
        self.data = data
        self.calls: list[int] = []

    async def __call__(self, tmdb_id: int) -> dict[str, Any]:
        self.calls.append(tmdb_id)
        return self.data[tmdb_id]


def test_rank_descarta_poco_parecidos_y_usa_titulo_original() -> None:
    ranked = rank_by_similarity(
        "Inception",
        [cand(1, "Origen", "Inception"), cand(2, "Interstellar"), cand(3, "Inceptor")],
    )

    assert [s.candidate.tmdb_id for s in ranked] == [1, 3]
    assert ranked[0].similarity == 1.0


async def test_remake_se_desempata_por_netflix_es() -> None:
    # Dos "Total Recall": la popular (2012) no está en Netflix ES; la de 1990 sí.
    fetch = FakeDetails(
        {
            10: details_json(10, "Desafío total", "Total Recall", year=2012, popularity=90),
            20: details_json(20, "Desafío total", "Total Recall", year=1990, netflix_es=True),
        }
    )

    match = await choose_match(
        "Total Recall", [cand(10, "Total Recall", pop=90), cand(20, "Total Recall", pop=30)], fetch
    )

    assert match is not None
    assert match.tmdb_id == 20
    assert match.year == 1990
    assert match.netflix_es is True
    assert match.confidence == "alta"
    assert match.runtime_s == 148 * 60
    assert sorted(fetch.calls) == [10, 20]


async def test_homonimos_sin_netflix_dan_confianza_media() -> None:
    fetch = FakeDetails(
        {1: details_json(1, "Ghost", "Ghost"), 2: details_json(2, "Ghost", "Ghost")}
    )

    match = await choose_match("Ghost", [cand(1, "Ghost", pop=5), cand(2, "Ghost", pop=9)], fetch)

    assert match is not None
    assert match.tmdb_id == 2  # desempata la popularidad
    assert match.confidence == "media"


async def test_parecido_lejano_consulta_solo_el_mejor() -> None:
    fetch = FakeDetails({1: details_json(1, "La gran aventura", "The Big Adventures")})

    match = await choose_match(
        "The Big Adventure",
        [cand(1, "The Big Adventures"), cand(2, "Big Adventure Time II")],
        fetch,
    )

    assert match is not None
    assert fetch.calls == [1]


async def test_sin_candidatos_validos() -> None:
    fetch = FakeDetails({})

    assert await choose_match("Inception", [cand(1, "Something else")], fetch) is None
    assert await choose_match("Inception", [], fetch) is None
    assert fetch.calls == []


def test_confianza() -> None:
    assert confidence_for(1.0, True, 3) == "alta"
    assert confidence_for(1.0, False, 1) == "alta"
    assert confidence_for(1.0, False, 2) == "media"
    assert confidence_for(0.9, True, 0) == "media"
    assert confidence_for(0.7, True, 0) == "baja"
