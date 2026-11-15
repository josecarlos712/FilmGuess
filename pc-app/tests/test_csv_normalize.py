"""Tests del lector del CSV y de la normalización de títulos."""

from __future__ import annotations

from pathlib import Path

import pytest

from filmguess.catalog.csv_source import CsvSourceError, read_movies_csv
from filmguess.catalog.normalize import normalize_title, title_similarity, tokenize


def test_lee_csv_con_comas_entre_comillas_y_descarta_malas(tmp_path: Path) -> None:
    path = tmp_path / "movies.csv"
    path.write_bytes(
        b'"1,000 Times Good Night",80001898\r\n'
        b'"Inception",70131314\r\n'
        b'"Repetida",70131314\r\n'
        b'"Sin id",abc\r\n'
        b"\r\n"
        b'"",123\r\n'
    )

    result = read_movies_csv(path)

    assert [(m.title, m.netflix_id) for m in result.movies] == [
        ("1,000 Times Good Night", 80001898),
        ("Inception", 70131314),
    ]
    assert result.skipped_duplicate == 1
    assert result.skipped_invalid == 3


def test_csv_inexistente(tmp_path: Path) -> None:
    with pytest.raises(CsvSourceError):
        read_movies_csv(tmp_path / "no.csv")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Orígen", "origen"),
        ("The Lord of the Rings: The Fellowship", "lord of the rings the fellowship"),
        ("Fast & Furious", "fast and furious"),
        ("  ¡Átame!  ", "atame"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_similitud() -> None:
    assert title_similarity("The Matrix", "Matrix") == 1.0
    assert title_similarity("Inception", "Inceptión") == 1.0
    assert title_similarity("Inception", "Interstellar") < 0.6
    assert title_similarity("", "algo") == 0.0


def test_tokenize_quita_signos_y_acentos() -> None:
    assert tokenize('Orí"gen* (2010)') == ["ori", "gen", "2010"]
