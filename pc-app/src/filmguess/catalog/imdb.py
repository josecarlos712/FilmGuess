"""Valoraciones de IMDb a partir del fichero público ``title.ratings.tsv.gz``.

Fuente: IMDb Non-Commercial Datasets (uso personal y no comercial).
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path

import httpx

from filmguess.catalog.db import CatalogDB

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"


class ImdbError(Exception):
    """No se pudo descargar o leer el fichero de valoraciones."""


def download_ratings(dest: Path, url: str = RATINGS_URL) -> Path:
    """Descarga el fichero de valoraciones de IMDb (unos 8 MB).

    Args:
        dest: Ruta de destino del ``.tsv.gz``.
        url: URL de origen.

    Returns:
        La ruta descargada.

    Raises:
        ImdbError: Si la descarga falla.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        tmp.replace(dest)
    except (httpx.HTTPError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise ImdbError(f"No se pudo descargar {url}: {exc}") from exc
    return dest


def iter_ratings(path: Path, wanted: set[str] | None = None) -> Iterator[tuple[str, float, int]]:
    """Lee el TSV comprimido de valoraciones.

    Args:
        path: Ruta del ``title.ratings.tsv.gz``.
        wanted: Si se indica, solo devuelve esos IMDb IDs.

    Yields:
        Tuplas ``(imdb_id, nota, votos)``.

    Raises:
        ImdbError: Si el fichero no existe o no tiene el formato esperado.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            header = handle.readline().rstrip("\n").split("\t")
            if header[:3] != ["tconst", "averageRating", "numVotes"]:
                raise ImdbError(f"Cabecera inesperada en {path}: {header}")
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3 or (wanted is not None and parts[0] not in wanted):
                    continue
                try:
                    yield parts[0], float(parts[1]), int(parts[2])
                except ValueError:
                    continue
    except (OSError, EOFError) as exc:
        raise ImdbError(f"No se pudo leer {path}: {exc}") from exc


def apply_ratings(db: CatalogDB, path: Path) -> int:
    """Actualiza en el catálogo las valoraciones de IMDb.

    Args:
        db: Catálogo.
        path: Ruta del ``title.ratings.tsv.gz``.

    Returns:
        Número de películas actualizadas.

    Raises:
        ImdbError: Si el fichero no se puede leer.
    """
    return db.update_imdb_ratings(iter_ratings(path, db.imdb_ids()))
