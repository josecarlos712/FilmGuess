"""Catálogo incluido: exportación sin datos privados e instalación al primer arranque."""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.seed import SeedError, export_seed, install_seed


def _catalog(path: Path) -> None:
    db = CatalogDB(path)
    db.close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO movies (netflix_id, csv_title, imdb_rating, imdb_votes) "
            "VALUES (1, 'Origen', 8.8, 2500000)"
        )
        conn.execute("INSERT INTO random_history (netflix_id, picked_at) VALUES (1, '2026-11-16')")
    conn.close()


def _rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _unzip(seed: Path, dest: Path) -> Path:
    dest.write_bytes(gzip.decompress(seed.read_bytes()))
    return dest


def test_export_strips_history_and_imdb(tmp_path: Path) -> None:
    source = tmp_path / "catalogo.sqlite"
    _catalog(source)
    summary = export_seed(source, tmp_path / "base.sqlite.gz")
    assert (summary.movies, summary.clues) == (1, 0)
    copy = _unzip(summary.path, tmp_path / "copia.sqlite")
    assert _rows(copy, "SELECT imdb_rating, imdb_votes FROM movies") == [(None, None)]
    assert _rows(copy, "SELECT count(*) FROM random_history") == [(0,)]
    assert _rows(source, "SELECT count(*) FROM random_history") == [(1,)]


def test_export_can_keep_imdb(tmp_path: Path) -> None:
    source = tmp_path / "catalogo.sqlite"
    _catalog(source)
    summary = export_seed(source, tmp_path / "base.sqlite.gz", keep_imdb=True)
    copy = _unzip(summary.path, tmp_path / "copia.sqlite")
    assert _rows(copy, "SELECT imdb_votes FROM movies") == [(2500000,)]


def test_export_without_source_fails(tmp_path: Path) -> None:
    with pytest.raises(SeedError):
        export_seed(tmp_path / "no.sqlite", tmp_path / "base.sqlite.gz")


def test_install_when_missing_or_empty(tmp_path: Path) -> None:
    source = tmp_path / "origen.sqlite"
    _catalog(source)
    seed = export_seed(source, tmp_path / "base.sqlite.gz").path

    target = tmp_path / "data" / "catalogo.sqlite"
    assert install_seed(target, seed) is True
    assert _rows(target, "SELECT csv_title FROM movies") == [("Origen",)]

    empty = tmp_path / "vacio.sqlite"
    CatalogDB(empty).close()
    assert install_seed(empty, seed) is True
    assert _rows(empty, "SELECT count(*) FROM movies") == [(1,)]


def test_install_never_overwrites_existing_catalog(tmp_path: Path) -> None:
    source = tmp_path / "origen.sqlite"
    _catalog(source)
    seed = export_seed(source, tmp_path / "base.sqlite.gz").path
    mine = tmp_path / "mio.sqlite"
    _catalog(mine)
    conn = sqlite3.connect(mine)
    with conn:
        conn.execute("UPDATE movies SET csv_title = 'Mía'")
    conn.close()
    assert install_seed(mine, seed) is False
    assert _rows(mine, "SELECT csv_title FROM movies") == [("Mía",)]


def test_install_without_seed_does_nothing(tmp_path: Path) -> None:
    target = tmp_path / "catalogo.sqlite"
    assert install_seed(target, tmp_path / "no.sqlite.gz") is False
    assert not target.exists()


def test_install_rejects_damaged_seed(tmp_path: Path) -> None:
    seed = tmp_path / "roto.sqlite.gz"
    seed.write_bytes(b"no es gzip")
    target = tmp_path / "catalogo.sqlite"
    with pytest.raises(SeedError):
        install_seed(target, seed)
    assert not target.exists()
    assert not (tmp_path / "catalogo.sqlite.nuevo").exists()
