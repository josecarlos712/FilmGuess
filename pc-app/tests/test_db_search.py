"""Tests de la base de datos del catálogo y de la búsqueda."""

from __future__ import annotations

import pytest

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.search import build_fts_query, search_movies
from tests.conftest import make_match


@pytest.fixture
def filled(db: CatalogDB) -> CatalogDB:
    db.add_csv_movies(
        [
            CsvMovie(70131314, "Inception"),
            CsvMovie(70000001, "Origin Unknown"),
            CsvMovie(70000002, "The Origin of Species"),
        ]
    )
    db.save_match(70131314, make_match(1))
    db.save_match(
        70000001,
        make_match(
            2, title_es="Origen desconocido", original_title="Origin Unknown", netflix_es=False
        ),
    )
    return db


def test_add_csv_ignora_existentes(db: CatalogDB) -> None:
    assert db.add_csv_movies([CsvMovie(1, "A"), CsvMovie(2, "B")]) == 2
    assert db.add_csv_movies([CsvMovie(2, "B"), CsvMovie(3, "C")]) == 1
    assert db.count() == 3
    assert [p[0] for p in db.pending()] == [1, 2, 3]
    assert db.pending(limit=1) == [(1, "A", None)]


@pytest.mark.parametrize("q", ["origen", "Orígen", "ORIG", "incep", "inception"])
def test_busqueda_sin_acentos_por_varios_titulos(filled: CatalogDB, q: str) -> None:
    page = search_movies(filled, q)

    assert [m.netflix_id for m in page.results] == [70131314]
    assert page.results[0].display_title == "Origen"


def test_busqueda_incluye_no_confirmadas(filled: CatalogDB) -> None:
    page = search_movies(filled, "origen", include_unconfirmed=True)

    assert {m.netflix_id for m in page.results} == {70131314, 70000001}
    assert page.total == 2


def test_busqueda_sin_palabras(filled: CatalogDB) -> None:
    assert search_movies(filled, '  *"()  ').total == 0
    assert build_fts_query("") is None


def test_build_fts_query_es_segura() -> None:
    assert build_fts_query('orig" OR 1=1 --') == '"orig"* "or"* "1"* "1"*'


def test_paginacion(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(i, f"Star film {i}") for i in range(1, 31)])

    first = search_movies(db, "star", include_unconfirmed=True, page_size=10)
    last = search_movies(db, "star", include_unconfirmed=True, page=3, page_size=10)

    assert first.total == 30
    assert len(first.results) == 10
    assert len(last.results) == 10
    assert {m.netflix_id for m in first.results}.isdisjoint({m.netflix_id for m in last.results})


def test_correccion_manual_no_se_pisa(filled: CatalogDB) -> None:
    filled.save_match(70131314, make_match(99, title_es="Manual"), manual=True)
    filled.save_match(70131314, make_match(5, title_es="Automática"))
    filled.mark_no_match(70131314)

    movie = filled.get(70131314)
    assert movie is not None
    assert movie.title_es == "Manual"
    assert movie.manual
    assert movie.match_conf == "alta"


def test_fts_se_actualiza_al_cambiar_titulo(filled: CatalogDB) -> None:
    filled.save_match(70131314, make_match(1, title_es="Nombre nuevo"))

    assert search_movies(filled, "nuevo").total == 1
    assert search_movies(filled, "origen").total == 0  # 70000001 no está en ES


def test_netflix_es_y_stats(filled: CatalogDB) -> None:
    filled.set_netflix_es(70131314, False)

    stats = filled.stats()
    assert stats["total"] == 3
    assert stats["pendientes"] == 1
    assert stats["alta"] == 2
    assert stats["netflix_es"] == 0


def test_imdb_ratings(filled: CatalogDB) -> None:
    filled.save_match(70131314, make_match(1, imdb_id="tt1375666"))

    assert filled.imdb_ids() == {"tt1375666"}
    assert filled.update_imdb_ratings([("tt1375666", 8.8, 2_500_000), ("tt9", 1.0, 1)]) == 1
    movie = filled.get(70131314)
    assert movie is not None
    assert (movie.imdb_rating, movie.imdb_votes) == (8.8, 2_500_000)
