"""Tests del origen (idioma y país) y de su exclusión del modo aleatorio."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.origin import fetch_origin, parse_origin
from filmguess.catalog.random_pick import pick_random
from filmguess.catalog.tmdb import API_BASE, TmdbAuthError, TmdbClient
from filmguess.cli import main
from filmguess.config import RandomConfig
from tests.conftest import make_match


def _seed(db: CatalogDB, ids: list[int]) -> None:
    db.add_csv_movies([CsvMovie(i, f"P{i}") for i in ids])
    for i in ids:
        db.save_match(i, make_match(i, imdb_id=f"tt{i}"))


def test_migracion_anade_columnas() -> None:
    db = CatalogDB(":memory:")
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(movies)")}
    db.close()
    assert {"original_language", "origin_country", "origin_fetched_at"} <= cols


def test_save_origin_y_origin_targets(db: CatalogDB) -> None:
    _seed(db, [1, 2])
    assert len(db.origin_targets()) == 2

    db.save_origin(1, "hi", ["IN"])

    assert [t.netflix_id for t in db.origin_targets()] == [2]
    assert len(db.origin_targets(refresh=True)) == 2
    row = db._conn.execute(
        "SELECT original_language, origin_country FROM movies WHERE netflix_id = 1"
    ).fetchone()
    assert tuple(row) == ("hi", "IN")


def test_exclusion_en_el_azar(db: CatalogDB) -> None:
    _seed(db, [1, 2, 3, 4])
    db.save_origin(1, "hi", ["IN"])  # por idioma y país
    db.save_origin(2, "en", ["US", "CN"])  # solo por país
    db.save_origin(3, "en", ["US"])
    # 4: origen desconocido (NULL), se conserva
    config = RandomConfig(
        min_votes=0,
        exclude_languages=["hi", "zh"],
        exclude_countries=["IN", "CN", "HK"],
    )

    ids = {cast(Any, pick_random(db, config, random.Random(s))).netflix_id for s in range(30)}
    assert ids == {3, 4}

    only_lang = RandomConfig(min_votes=0, exclude_languages=["hi"])
    ids = {cast(Any, pick_random(db, only_lang, random.Random(s))).netflix_id for s in range(40)}
    assert ids == {2, 3, 4}


def test_parse_origin() -> None:
    assert parse_origin({"original_language": "hi", "origin_country": ["IN"]}) == ("hi", ["IN"])
    data = {
        "original_language": "zh",
        "origin_country": [],
        "production_countries": [{"iso_3166_1": "HK"}, {"iso_3166_1": "CN"}],
    }
    assert parse_origin(data) == ("zh", ["HK", "CN"])
    assert parse_origin({}) == (None, [])


@respx.mock
async def test_fetch_origin(db: CatalogDB) -> None:
    _seed(db, [1, 2, 3])
    respx.get(f"{API_BASE}/movie/1").mock(
        return_value=httpx.Response(200, json={"original_language": "hi", "origin_country": ["IN"]})
    )
    respx.get(f"{API_BASE}/movie/2").mock(return_value=httpx.Response(404))
    respx.get(f"{API_BASE}/movie/3").mock(return_value=httpx.Response(500))
    client = TmdbClient("clave_v3", per_second=0)

    progress = await fetch_origin(db, client)

    assert progress.done == 3 and progress.errors == 1
    assert [t.netflix_id for t in db.origin_targets()] == [3]


@respx.mock
async def test_fetch_origin_clave_invalida(db: CatalogDB) -> None:
    _seed(db, [1])
    respx.get(f"{API_BASE}/movie/1").mock(return_value=httpx.Response(401))
    with pytest.raises(TmdbAuthError):
        await fetch_origin(db, TmdbClient("clave_v3", per_second=0))


def test_config_por_defecto_vacia() -> None:
    config = RandomConfig()
    assert config.exclude_languages == [] and config.exclude_countries == []


def test_cli_origen_registrado(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[catalog]\ndata_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert main(["--config", str(config), "origen", "--limite", "1"]) == 1
    assert capsys.readouterr().out.strip()
