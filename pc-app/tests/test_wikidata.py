"""Tests de la fuente Wikidata y del enriquecimiento por ID de TMDB."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.importer import enrich_pending, ingest_wikidata
from filmguess.catalog.tmdb import TmdbCandidate, TmdbClient, TmdbNotFoundError
from filmguess.catalog.wikidata import (
    WikidataError,
    WikidataMovie,
    download_wikidata,
    parse_wikidata_csv,
    read_wikidata_csv,
)
from tests.conftest import details_json, make_match

SAMPLE = """netflix,tmdb,imdb
80000001,550,tt0137523
80000001,551,tt0000002
abc,1,tt1
80000002,,tt0000003
80000003,777,
"""


class IdTmdb:
    """TMDB falso: conoce algunos IDs y registra qué se le pide."""

    region = "ES"

    def __init__(self, known: set[int]) -> None:
        self.known = known
        self.details_calls: list[int] = []
        self.searches: list[str] = []

    async def search_movies(self, query: str) -> list[TmdbCandidate]:
        self.searches.append(query)
        return [TmdbCandidate(42, query, query, 2000, 1)]

    async def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        self.details_calls.append(tmdb_id)
        if tmdb_id not in self.known and tmdb_id != 42:
            raise TmdbNotFoundError(f"TMDB no encuentra /movie/{tmdb_id} (404).")
        return details_json(tmdb_id, f"Título {tmdb_id}", "Original", netflix_es=True)


def as_client(fake: IdTmdb) -> TmdbClient:
    return cast(TmdbClient, fake)


def test_parse_descarta_no_numericos_y_repetidos() -> None:
    movies = parse_wikidata_csv(SAMPLE)

    assert movies == [
        WikidataMovie(80000001, 550, "tt0137523"),
        WikidataMovie(80000002, None, "tt0000003"),
        WikidataMovie(80000003, 777, None),
    ]


def test_parse_sin_columna_netflix_falla() -> None:
    with pytest.raises(WikidataError, match="netflix"):
        parse_wikidata_csv("a,b\n1,2\n")


def test_read_fichero_inexistente(tmp_path: Path) -> None:
    with pytest.raises(WikidataError, match="No se pudo leer"):
        read_wikidata_csv(tmp_path / "no.csv")


def test_download_guarda_el_csv(tmp_path: Path) -> None:
    response = MagicMock(content=SAMPLE.encode())
    dest = tmp_path / "wd.csv"
    with patch("filmguess.catalog.wikidata.httpx.get", return_value=response) as get:
        download_wikidata(dest, min_rows=1)

    assert dest.read_text(encoding="utf-8") == SAMPLE
    assert get.call_args.kwargs["headers"]["Accept"] == "text/csv"


def test_download_error_no_deja_fichero(tmp_path: Path) -> None:
    dest = tmp_path / "wd.csv"
    with (
        patch("filmguess.catalog.wikidata.httpx.get", side_effect=httpx.ConnectError("sin red")),
        pytest.raises(WikidataError, match="sin red"),
    ):
        download_wikidata(dest)
    assert not dest.exists()
    assert not dest.with_suffix(".csv.part").exists()


def test_add_wikidata_anade_solo_con_tmdb_y_enlaza_existentes(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "Inception")])

    added, reopened = db.add_wikidata_movies(
        [WikidataMovie(1, 27205, None), WikidataMovie(2, 550, None), WikidataMovie(3, None, None)]
    )

    assert (added, reopened) == (1, 0)
    assert db.pending() == [(1, "Inception", 27205), (2, "", 550)]


def test_add_wikidata_reabre_emparejamientos_que_no_coinciden(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "MOVIE"), CsvMovie(2, "Inception"), CsvMovie(3, "Fija")])
    db.save_match(1, make_match(5559, confidence="baja"))  # falso: «Bee Movie»
    db.save_match(2, make_match(27205))
    db.save_match(3, make_match(9, confidence="baja"), manual=True)

    added, reopened = db.add_wikidata_movies(
        [WikidataMovie(1, 651800, None), WikidataMovie(2, 27205, None), WikidataMovie(3, 8, None)]
    )

    assert (added, reopened) == (0, 1)
    assert db.pending() == [(1, "MOVIE", 651800)]
    # Repetir la misma ingesta no reabre nada (evita reintentar sin fin un ID inexistente).
    db.mark_no_match(1)
    assert db.add_wikidata_movies([WikidataMovie(1, 651800, None)]) == (0, 0)


def test_ingest_wikidata_desde_fichero(db: CatalogDB, tmp_path: Path) -> None:
    path = tmp_path / "wd.csv"
    path.write_text(SAMPLE, encoding="utf-8")

    assert ingest_wikidata(db, path) == (2, 0)


async def test_enrich_por_id_no_busca_por_titulo(db: CatalogDB) -> None:
    db.add_wikidata_movies([WikidataMovie(10, 550, None)])
    fake = IdTmdb({550})

    progress = await enrich_pending(db, as_client(fake), workers=1)

    movie = db.get(10)
    assert progress.matched == 1
    assert fake.searches == []
    assert movie is not None
    assert (movie.tmdb_id, movie.title_es, movie.match_conf) == (550, "Título 550", "alta")
    assert movie.netflix_es is True


async def test_enrich_id_inexistente_busca_por_titulo_o_marca_sin_match(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "Inception")])
    db.add_wikidata_movies([WikidataMovie(1, 999, None), WikidataMovie(2, 998, None)])
    fake = IdTmdb(set())

    progress = await enrich_pending(db, as_client(fake), workers=1)

    assert (progress.matched, progress.no_match, progress.errors) == (1, 1, 0)
    assert fake.searches == ["Inception"]
    assert db.get(1) is not None and db.get(1).tmdb_id == 42  # type: ignore[union-attr]
    assert db.get(2) is not None and db.get(2).match_conf == "sin_match"  # type: ignore[union-attr]


def test_pending_prioriza_los_que_tienen_id(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "A"), CsvMovie(5, "B")])
    db.add_wikidata_movies([WikidataMovie(5, 77, None)])

    assert [p.netflix_id for p in db.pending()] == [5, 1]


def test_migracion_anade_columna(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "viejo.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE movies (netflix_id INTEGER PRIMARY KEY, csv_title TEXT NOT NULL,"
        " title_es TEXT, original_title TEXT, year INTEGER, runtime_s INTEGER,"
        " poster_path TEXT, tmdb_id INTEGER, imdb_id TEXT, imdb_rating REAL,"
        " imdb_votes INTEGER, tmdb_popularity REAL, tmdb_vote_avg REAL,"
        " tmdb_vote_count INTEGER, netflix_es INTEGER, match_conf TEXT,"
        " manual INTEGER NOT NULL DEFAULT 0, updated_at TEXT)"
    )
    conn.execute("INSERT INTO movies(netflix_id, csv_title) VALUES (1, 'A')")
    conn.commit()
    conn.close()

    db = CatalogDB(path)
    try:
        assert db.pending() == [(1, "A", None)]
    finally:
        db.close()


def test_download_reintenta_si_la_respuesta_llega_cortada(tmp_path: Path) -> None:
    cut = MagicMock(content=b"netflix,tmdb,imdb\n80000001,550,tt0137523\n4491")
    full = MagicMock(content=SAMPLE.encode())
    dest = tmp_path / "wd.csv"
    with patch("filmguess.catalog.wikidata.httpx.get", side_effect=[cut, full]) as get:
        download_wikidata(dest, min_rows=1)

    assert get.call_count == 2
    assert dest.read_text(encoding="utf-8") == SAMPLE


def test_download_falla_si_siempre_faltan_filas(tmp_path: Path) -> None:
    dest = tmp_path / "wd.csv"
    with (
        patch(
            "filmguess.catalog.wikidata.httpx.get",
            return_value=MagicMock(content=SAMPLE.encode()),
        ),
        pytest.raises(WikidataError, match="incompleta"),
    ):
        download_wikidata(dest, attempts=2, min_rows=100)
    assert not dest.exists()
