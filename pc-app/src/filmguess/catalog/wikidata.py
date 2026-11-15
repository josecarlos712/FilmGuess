"""IDs de Netflix enlazados con TMDB e IMDb, sacados de Wikidata.

Wikidata guarda el ID de Netflix (P1874) de decenas de miles de títulos junto a su ID de
película en TMDB (P4947) y de IMDb (P345). Con ellos la película se enriquece por ID, sin
emparejar por título. Los datos son CC0.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "FilmGuessCatalog/0.1 (https://www.wikidata.org/wiki/Wikidata:Data_access)"
QUERY = """SELECT ?netflix ?tmdb ?imdb WHERE {
  ?item wdt:P1874 ?netflix .
  OPTIONAL { ?item wdt:P4947 ?tmdb }
  OPTIONAL { ?item wdt:P345 ?imdb }
}"""
MIN_ROWS = 10_000
"""En 2026 la consulta devuelve unas 32.800 filas; muchas menos indica un corte."""


class WikidataError(Exception):
    """No se pudo descargar o leer el fichero de Wikidata."""


@dataclass(frozen=True, slots=True)
class WikidataMovie:
    """Un título de Netflix según Wikidata."""

    netflix_id: int
    tmdb_id: int | None
    imdb_id: str | None


def download_wikidata(
    dest: Path,
    url: str = SPARQL_URL,
    timeout: float = 300,
    attempts: int = 3,
    min_rows: int = MIN_ROWS,
) -> Path:
    """Descarga de Wikidata la tabla Netflix ↔ TMDB ↔ IMDb como CSV (unos 800 kB).

    El servicio a veces corta la respuesta a medias aunque devuelva HTTP 200. Se reconoce
    porque la última línea queda sin terminar o faltan filas, y entonces se reintenta.

    Args:
        dest: Ruta de destino del CSV.
        url: Punto de consulta SPARQL.
        timeout: Segundos máximos de espera por intento.
        attempts: Intentos como máximo.
        min_rows: Filas mínimas para dar la respuesta por completa.

    Returns:
        La ruta descargada.

    Raises:
        WikidataError: Si la descarga falla o llega incompleta en todos los intentos.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"Accept": "text/csv", "User-Agent": USER_AGENT}
    problem = "sin respuesta"
    for _ in range(max(1, attempts)):
        try:
            response = httpx.get(url, params={"query": QUERY}, headers=headers, timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            problem = str(exc)
            continue
        content = response.content
        rows = content.count(b"\n") - 1
        if not content.endswith(b"\n") or rows < min_rows:
            problem = f"respuesta incompleta ({max(rows, 0)} filas)"
            continue
        try:
            tmp.write_bytes(content)
            tmp.replace(dest)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise WikidataError(f"No se pudo guardar {dest}: {exc}") from exc
        return dest
    raise WikidataError(f"No se pudo descargar la consulta de Wikidata: {problem}")


def parse_wikidata_csv(text: str) -> list[WikidataMovie]:
    """Lee la respuesta CSV de la consulta.

    Descarta los IDs de Netflix no numéricos y se queda con la primera fila de cada ID
    (un mismo título puede salir repetido si tiene varios IDs de TMDB o IMDb).

    Args:
        text: Contenido del CSV con columnas ``netflix``, ``tmdb`` e ``imdb``.

    Returns:
        Un elemento por ID de Netflix, en el orden del fichero.

    Raises:
        WikidataError: Si faltan las columnas esperadas.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "netflix" not in reader.fieldnames:
        raise WikidataError("El CSV de Wikidata no tiene la columna «netflix».")
    seen: dict[int, WikidataMovie] = {}
    for row in reader:
        netflix = (row.get("netflix") or "").strip()
        if not netflix.isdigit():
            continue
        netflix_id = int(netflix)
        if netflix_id in seen:
            continue
        tmdb = (row.get("tmdb") or "").strip()
        imdb = (row.get("imdb") or "").strip()
        seen[netflix_id] = WikidataMovie(
            netflix_id=netflix_id,
            tmdb_id=int(tmdb) if tmdb.isdigit() else None,
            imdb_id=imdb if imdb.startswith("tt") else None,
        )
    return list(seen.values())


def read_wikidata_csv(path: Path) -> list[WikidataMovie]:
    """Lee el CSV descargado.

    Args:
        path: Ruta del CSV.

    Returns:
        Los títulos de Netflix con sus IDs.

    Raises:
        WikidataError: Si el fichero no se puede leer.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WikidataError(f"No se pudo leer {path}: {exc}") from exc
    return parse_wikidata_csv(text)
