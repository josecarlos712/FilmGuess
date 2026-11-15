"""Tests del importador, de IMDb y de la película aleatoria."""

from __future__ import annotations

import gzip
import itertools
import random
from pathlib import Path
from typing import Any, cast

import pytest

from filmguess.catalog.csv_source import CsvMovie
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.imdb import ImdbError, apply_ratings, iter_ratings
from filmguess.catalog.importer import enrich_pending, ingest_csv
from filmguess.catalog.random_pick import pick_random, rank_candidates, weighted_rating
from filmguess.catalog.tmdb import TmdbAuthError, TmdbCandidate, TmdbClient, TmdbError
from filmguess.cli import main
from filmguess.config import RandomConfig
from tests.conftest import details_json, make_match


class FakeTmdb:
    region = "ES"

    def __init__(self, fail_titles: set[str] | None = None, auth_error: bool = False) -> None:
        self.fail_titles = fail_titles or set()
        self.auth_error = auth_error

    async def search_movies(self, query: str) -> list[TmdbCandidate]:
        if self.auth_error:
            raise TmdbAuthError("TMDB rechaza la clave (401).")
        if query in self.fail_titles:
            raise TmdbError("HTTP 503")
        if query == "Nada parecido":
            return [TmdbCandidate(9, "Otra cosa", "Otra cosa", 2000, 1)]
        return [TmdbCandidate(hash(query) % 10000, query, query, 2000, 1)]

    async def movie_details(self, tmdb_id: int) -> dict[str, Any]:
        return details_json(tmdb_id, f"Título {tmdb_id}", "Original", netflix_es=True)


def as_client(fake: FakeTmdb) -> TmdbClient:
    return cast(TmdbClient, fake)


async def test_enrich_pending_guarda_y_deja_pendientes_los_fallos(db: CatalogDB) -> None:
    db.add_csv_movies(
        [CsvMovie(1, "Inception"), CsvMovie(2, "Nada parecido"), CsvMovie(3, "Falla")]
    )
    seen: list[int] = []

    progress = await enrich_pending(
        db, as_client(FakeTmdb({"Falla"})), workers=2, on_progress=lambda p: seen.append(p.done)
    )

    assert (progress.matched, progress.no_match, progress.errors) == (1, 1, 1)
    assert sorted(seen) == [1, 2, 3]
    assert db.get(1) is not None and db.get(1).match_conf == "alta"  # type: ignore[union-attr]
    assert db.get(2) is not None and db.get(2).match_conf == "sin_match"  # type: ignore[union-attr]
    assert db.pending() == [(3, "Falla", None)]  # se reintentará en la próxima ejecución


async def test_enrich_pending_respeta_limite(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(i, f"Peli {i}") for i in range(1, 6)])

    progress = await enrich_pending(db, as_client(FakeTmdb()), limit=2)

    assert progress.total == 2
    assert len(db.pending()) == 3


async def test_clave_no_valida_detiene_la_importacion(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "A"), CsvMovie(2, "B")])

    with pytest.raises(TmdbError, match="detenida"):
        await enrich_pending(db, as_client(FakeTmdb(auth_error=True)), workers=1)
    assert len(db.pending()) == 2


def test_ingest_csv(db: CatalogDB, tmp_path: Path) -> None:
    csv_path = tmp_path / "m.csv"
    csv_path.write_text('"A",1\n"B",2\n', encoding="utf-8")

    assert ingest_csv(db, csv_path) == 2
    assert ingest_csv(db, csv_path) == 0


def _ratings_file(tmp_path: Path, header: str = "tconst\taverageRating\tnumVotes") -> Path:
    path = tmp_path / "ratings.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(f"{header}\ntt1\t8.8\t2500000\ntt2\t6.0\t10\ntt3\tmal\t1\n")
    return path


def test_iter_y_apply_ratings(db: CatalogDB, tmp_path: Path) -> None:
    path = _ratings_file(tmp_path)
    db.add_csv_movies([CsvMovie(1, "A")])
    db.save_match(1, make_match(1, imdb_id="tt1"))

    assert list(iter_ratings(path, {"tt2", "tt3"})) == [("tt2", 6.0, 10)]
    assert apply_ratings(db, path) == 1
    assert db.get(1).imdb_votes == 2500000  # type: ignore[union-attr]


def test_ratings_con_cabecera_erronea(tmp_path: Path) -> None:
    with pytest.raises(ImdbError):
        list(iter_ratings(_ratings_file(tmp_path, header="otra\tcosa\taqui")))


def test_weighted_rating() -> None:
    assert weighted_rating(9.0, 0, 100, 7.0) == 7.0
    assert weighted_rating(9.0, 100, 100, 7.0) == 8.0
    assert weighted_rating(5.0, 0, 0, 7.0) == 7.0


def _seed_random(db: CatalogDB) -> None:
    rows = [
        # id, año, votos IMDb, nota, ES
        (1, 2010, 2_000_000, 8.8, True),
        (2, 1999, 1_000_000, 8.7, True),
        (3, 1985, 1_000_000, 8.5, True),  # anterior a 1991
        (4, 2015, 1_000, 9.9, True),  # poco conocida
        (5, 2005, 500_000, 7.0, False),  # no disponible en ES
        (6, 2020, 300_000, 6.0, True),
    ]
    db.add_csv_movies([CsvMovie(r[0], f"P{r[0]}") for r in rows])
    for netflix_id, year, votes, rating, es in rows:
        db.save_match(
            netflix_id,
            make_match(netflix_id, year=year, netflix_es=es, imdb_id=f"tt{netflix_id}"),
        )
        db.update_imdb_ratings([(f"tt{netflix_id}", rating, votes)])


def test_rank_y_pick_random(db: CatalogDB) -> None:
    _seed_random(db)
    config = RandomConfig(min_year=1991, min_votes=25000, pool_size=2, no_repeat=1)

    ranked = rank_candidates(db.random_candidates(min_year=1991), 25000)
    assert [r.movie.netflix_id for r in ranked] == [1, 2, 6]

    rng = random.Random(0)
    picks = [pick_random(db, config, rng) for _ in range(6)]
    ids = [p.netflix_id for p in picks if p]
    assert set(ids) <= {1, 2}
    assert all(a != b for a, b in itertools.pairwise(ids))  # no repite la anterior


def test_random_usa_tmdb_sin_datos_imdb(db: CatalogDB) -> None:
    db.add_csv_movies([CsvMovie(1, "A"), CsvMovie(2, "B")])
    db.save_match(1, make_match(1, vote_count=5000, vote_avg=8.0))
    db.save_match(2, make_match(2, vote_count=10, vote_avg=9.0))

    picked = pick_random(db, RandomConfig(), random.Random(1))

    assert picked is not None and picked.netflix_id == 1


def test_random_sin_candidatas(db: CatalogDB) -> None:
    assert pick_random(db, RandomConfig()) is None


def test_cli_importar_solo_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "movies.csv").write_text('"Inception",70131314\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'[catalog]\ndata_dir = "{tmp_path.as_posix()}"\n'
        f'csv_file = "{(tmp_path / "movies.csv").as_posix()}"\n',
        encoding="utf-8",
    )

    code = main(["--config", str(config), "importar", "--sin-tmdb"])

    out = capsys.readouterr().out
    assert code == 0
    assert "CSV: 1 películas nuevas." in out
    assert "--descargar-imdb" in out
    assert (tmp_path / "catalogo.sqlite").exists()


def test_cli_importar_sin_clave_tmdb(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "movies.csv").write_text('"A",1\n', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'[catalog]\ndata_dir = "{tmp_path.as_posix()}"\n'
        f'csv_file = "{(tmp_path / "movies.csv").as_posix()}"\n',
        encoding="utf-8",
    )

    assert main(["--config", str(config), "importar", "--sin-imdb"]) == 1
    assert "Falta la clave" in capsys.readouterr().out
