"""Lectura del CSV de películas del usuario (``"Nombre",netflixId``)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CsvMovie:
    """Una fila válida del CSV."""

    netflix_id: int
    title: str


@dataclass(slots=True)
class CsvReadResult:
    """Resultado de leer el CSV."""

    movies: list[CsvMovie] = field(default_factory=list)
    skipped_invalid: int = 0
    skipped_duplicate: int = 0


class CsvSourceError(Exception):
    """El CSV no existe o no se puede leer."""


def read_movies_csv(path: Path) -> CsvReadResult:
    """Lee el CSV de películas, descartando filas no válidas o con ID repetido.

    El formato es ``"Nombre",id`` sin cabecera; el nombre puede contener comas
    porque va entre comillas.

    Args:
        path: Ruta del CSV.

    Returns:
        Las películas válidas (en el orden del fichero) y el recuento de descartes.

    Raises:
        CsvSourceError: Si el fichero no existe o no se puede decodificar.
    """
    result = CsvReadResult()
    seen: set[int] = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                movie = _parse_row(row)
                if movie is None:
                    result.skipped_invalid += 1
                elif movie.netflix_id in seen:
                    result.skipped_duplicate += 1
                else:
                    seen.add(movie.netflix_id)
                    result.movies.append(movie)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise CsvSourceError(f"No se pudo leer el CSV {path}: {exc}") from exc
    return result


def _parse_row(row: list[str]) -> CsvMovie | None:
    if len(row) != 2:
        return None
    title, raw_id = row[0].strip(), row[1].strip()
    if not title or not raw_id.isdigit():
        return None
    return CsvMovie(netflix_id=int(raw_id), title=title)
