"""Tests del catálogo multiplataforma."""

from __future__ import annotations

import csv
import gzip
import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from filmguess.catalog.platforms import (
    EXPORT_COLUMNS,
    SUBSCRIPTION_KINDS,
    PlatformsDB,
    apply_imdb_ratings,
    check_availability,
    discover_provider,
    export_csv,
    fill_imdb_ids,
    harvest,
    harvest_wikidata,
    parse_external_csv,
    wikidata_query,
)
from filmguess.catalog.tmdb import TmdbAuthError, TmdbClient, TmdbError, TmdbNotFoundError
from filmguess.catalog.wikidata import WikidataError


@pytest.fixture
def pdb() -> Iterator[PlatformsDB]:
    db = PlatformsDB(":memory:")
    yield db
    db.close()


def movie(tmdb_id: int, year: int = 2020) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": f"Peli {tmdb_id}",
        "original_title": f"Movie {tmdb_id}",
        "release_date": f"{year}-05-01",
        "popularity": float(tmdb_id),
        "vote_average": 7.0,
        "vote_count": 100,
        "poster_path": None,
    }


class FakeTmdb:
    """TMDB falso con un catálogo por plataforma y años de estreno."""

    region = "ES"

    def __init__(self, catalog: dict[int, list[dict[str, Any]]], per_page: int = 2) -> None:
        self.catalog = catalog
        self.per_page = per_page
        self.discover_calls: list[dict[str, str]] = []
        self.offers: dict[int, dict[str, Any]] = {}
        self.missing: set[int] = set()
        self.failing: set[int] = set()
        self.auth_error = False

    async def watch_providers(self) -> list[dict[str, Any]]:
        return [
            {"provider_id": 8, "provider_name": "Netflix", "display_priorities": {"ES": 1}},
            {"provider_id": 119, "provider_name": "Prime Video", "display_priorities": {"ES": 2}},
        ]

    async def discover_movies(self, params: dict[str, str]) -> dict[str, Any]:
        self.discover_calls.append(params)
        if self.auth_error:
            raise TmdbAuthError("401")
        items = self.catalog.get(int(params["with_watch_providers"]), [])
        if "primary_release_date.gte" in params:
            lo = int(params["primary_release_date.gte"][:4])
            hi = int(params["primary_release_date.lte"][:4])
            items = [m for m in items if lo <= int(m["release_date"][:4]) <= hi]
        page = int(params["page"])
        start = (page - 1) * self.per_page
        total_pages = -(-len(items) // self.per_page)
        return {
            "page": page,
            "total_pages": total_pages,
            "results": items[start : start + self.per_page],
        }

    async def movie_watch_providers(self, tmdb_id: int) -> dict[str, Any]:
        if tmdb_id in self.missing:
            raise TmdbNotFoundError("404")
        if tmdb_id in self.failing:
            raise TmdbError("HTTP 503")
        return {"results": {"ES": self.offers[tmdb_id]} if tmdb_id in self.offers else {}}


def as_client(fake: FakeTmdb) -> TmdbClient:
    return cast(TmdbClient, fake)


async def test_discover_recorre_todas_las_paginas(pdb: PlatformsDB) -> None:
    fake = FakeTmdb({8: [movie(i) for i in range(1, 6)]})

    count = await discover_provider(pdb, as_client(fake), 8)

    assert count == 5
    assert pdb.count_titles() == 5
    assert [c["page"] for c in fake.discover_calls] == ["1", "2", "3"]
    assert fake.discover_calls[0]["with_watch_monetization_types"] == "flatrate|free|ads"


async def test_discover_divide_por_anos_si_pasa_de_500_paginas(pdb: PlatformsDB) -> None:
    years = [1990, 2005, 2006, 2020]
    fake = FakeTmdb({8: [movie(i, y) for i, y in enumerate(years, 1)]}, per_page=1)

    with patch("filmguess.catalog.platforms.MAX_PAGES", 2):
        await discover_provider(pdb, as_client(fake), 8, last_year=2021)

    assert pdb.count_titles() == 4
    assert any("primary_release_date.gte" in c for c in fake.discover_calls)


async def test_check_availability_guarda_ofertas_y_reanuda(pdb: PlatformsDB) -> None:
    pdb.save_providers(await FakeTmdb({}).watch_providers(), "ES")
    pdb.upsert_titles([movie(1), movie(2), movie(3), movie(4)])
    fake = FakeTmdb({})
    fake.offers[1] = {"flatrate": [{"provider_id": 8}], "rent": [{"provider_id": 119}]}
    fake.missing.add(2)
    fake.failing.add(3)

    progress = await check_availability(pdb, as_client(fake), workers=1)

    assert (progress.done, progress.errors) == (4, 1)
    assert pdb.unchecked() == [3]  # el fallo se reintentará
    assert sorted(pdb.stats()) == [("Netflix", "flatrate", 1), ("Prime Video", "rent", 1)]
    pdb.reset_checks()
    assert len(pdb.unchecked()) == 4


async def test_check_availability_se_detiene_con_clave_invalida(pdb: PlatformsDB) -> None:
    pdb.upsert_titles([movie(1)])
    fake = FakeTmdb({})

    async def fail(tmdb_id: int) -> dict[str, Any]:
        raise TmdbAuthError("401")

    fake.movie_watch_providers = fail  # type: ignore[method-assign]
    with pytest.raises(TmdbError, match="detenida"):
        await check_availability(pdb, as_client(fake), workers=1)


def test_parse_external_csv() -> None:
    text = "ext,tmdb,imdb\nA1,10,tt1\nA1,11,tt2\nB2,,\n,5,tt3\n"

    assert parse_external_csv(text) == [("A1", 10, "tt1"), ("B2", None, None)]
    with pytest.raises(WikidataError):
        parse_external_csv("x\n1\n")
    assert "wdt:P8055" in wikidata_query("P8055")


def test_harvest_wikidata_sigue_si_falla_una_plataforma(pdb: PlatformsDB, tmp_path: Path) -> None:
    def fake_download(query: str, dest: Path, *args: object, **kwargs: object) -> Path:
        if "P2" in query:
            raise WikidataError("corte")
        dest.write_text("ext,tmdb,imdb\nX,1,tt1\nY,2,\n", encoding="utf-8")
        return dest

    with patch("filmguess.catalog.platforms.download_sparql_csv", side_effect=fake_download):
        saved, errors = harvest_wikidata(pdb, tmp_path, {"uno": "P1", "dos": "P2"})

    assert saved == {"uno": 2}
    assert errors == {"dos": "corte"}


async def test_harvest_completo(pdb: PlatformsDB, tmp_path: Path) -> None:
    fake = FakeTmdb({8: [movie(1), movie(2)], 119: [movie(2), movie(3)]})
    fake.offers = {
        1: {"flatrate": [{"provider_id": 8}]},
        2: {"flatrate": [{"provider_id": 8}, {"provider_id": 119}]},
        3: {"ads": [{"provider_id": 119}]},
    }

    summary = await harvest(pdb, as_client(fake), cache_dir=tmp_path, wikidata=False)

    assert (summary.providers, summary.discovered, summary.checked) == (2, 4, 3)
    assert sorted(pdb.stats()) == [
        ("Netflix", "flatrate", 2),
        ("Prime Video", "ads", 1),
        ("Prime Video", "flatrate", 1),
    ]


async def test_harvest_aborta_con_clave_invalida(pdb: PlatformsDB, tmp_path: Path) -> None:
    fake = FakeTmdb({8: [movie(1)]})
    fake.auth_error = True

    with pytest.raises(TmdbAuthError):
        await harvest(pdb, as_client(fake), cache_dir=tmp_path, wikidata=False)


async def test_fill_imdb_ids_usa_wikidata_y_luego_tmdb(pdb: PlatformsDB) -> None:
    pdb.upsert_titles([movie(1), movie(2), movie(3), movie(4)])
    pdb.replace_external_ids("netflix", [("80001", 1, "tt0000001")])
    fake = FakeTmdb({})
    asked: list[int] = []

    async def external_ids(tmdb_id: int) -> dict[str, Any]:
        asked.append(tmdb_id)
        if tmdb_id == 4:
            raise TmdbNotFoundError("404")
        return {"imdb_id": "tt0000002" if tmdb_id == 2 else None}

    fake.movie_external_ids = external_ids  # type: ignore[attr-defined]
    progress = await fill_imdb_ids(pdb, as_client(fake), workers=1)

    assert sorted(asked) == [2, 3, 4]  # la 1 ya la daba Wikidata
    assert progress.done == 3
    assert pdb.imdb_ids() == {"tt0000001", "tt0000002"}
    assert pdb.without_imdb_id() == []  # no se vuelven a consultar


def test_apply_ratings_y_export_csv(pdb: PlatformsDB, tmp_path: Path) -> None:
    pdb.save_providers(
        [
            {"provider_id": 8, "provider_name": "Netflix", "display_priorities": {"ES": 1}},
            {"provider_id": 119, "provider_name": "Prime Video", "display_priorities": {"ES": 2}},
        ],
        "ES",
    )
    pdb.upsert_titles([movie(1, 1999), movie(2, 2010)])
    pdb.save_availability(1, {"flatrate": [{"provider_id": 8}], "rent": [{"provider_id": 119}]})
    pdb.save_availability(2, {"ads": [{"provider_id": 119}]})
    pdb.set_imdb_id(1, "tt0000001")
    pdb.set_imdb_id(2, "tt0000002")
    pdb.replace_external_ids("netflix", [("80001", 1, None)])
    ratings = tmp_path / "r.tsv.gz"
    with gzip.open(ratings, "wt", encoding="utf-8") as handle:
        handle.write("tconst\taverageRating\tnumVotes\ntt0000001\t8.5\t900\ntt0000002\t6.1\t50\n")

    assert apply_imdb_ratings(pdb, ratings) == 2
    dest = tmp_path / "out.csv"
    assert export_csv(pdb, dest) == 3
    assert export_csv(pdb, tmp_path / "sus.csv", kinds=SUBSCRIPTION_KINDS) == 2

    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM para Excel
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == list(EXPORT_COLUMNS)
    assert rows[1][1:9] == [
        "Peli 1",
        "Movie 1",
        "1999",
        "Netflix",
        "suscripción",
        "tt0000001",
        "8,5",
        "900",
    ]
    assert rows[1][11] == "netflix:80001"
    assert rows[2][4:6] == ["Prime Video", "alquiler"]
    assert rows[3][4:9] == ["Prime Video", "con anuncios", "tt0000002", "6,1", "50"]


def test_migracion_anade_columnas_imdb(tmp_path: Path) -> None:
    path = tmp_path / "viejo.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE titles (tmdb_id INTEGER PRIMARY KEY, title TEXT, popularity REAL,"
        " checked_at TEXT)"
    )
    conn.execute("INSERT INTO titles(tmdb_id, title, popularity) VALUES (1, 'A', 1.0)")
    conn.commit()
    conn.close()

    db = PlatformsDB(path)
    try:
        assert db.without_imdb_id() == [1]
    finally:
        db.close()
