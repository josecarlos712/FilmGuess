"""Búsqueda de películas en el catálogo."""

from __future__ import annotations

from dataclasses import dataclass

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.models import Movie
from filmguess.catalog.normalize import tokenize

PAGE_SIZE = 24
MAX_TOKENS = 8


@dataclass(frozen=True, slots=True)
class SearchPage:
    """Una página de resultados."""

    results: list[Movie]
    total: int
    page: int
    page_size: int


def build_fts_query(text: str) -> str | None:
    """Convierte lo que escribe el usuario en una consulta FTS5 segura.

    Cada palabra se busca como prefijo (``"orig"*``) y todas deben aparecer.
    Las palabras solo contienen letras y números, así que no hay inyección de sintaxis.

    Args:
        text: Texto libre.

    Returns:
        La consulta, o ``None`` si no hay palabras buscables.
    """
    tokens = tokenize(text)[:MAX_TOKENS]
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens)


def search_movies(
    db: CatalogDB,
    text: str,
    *,
    page: int = 1,
    include_unconfirmed: bool = False,
    page_size: int = PAGE_SIZE,
    genre_id: int | None = None,
) -> SearchPage:
    """Busca por título en español, original o del CSV, sin distinguir acentos.

    Args:
        db: Catálogo.
        text: Texto buscado.
        page: Página (desde 1).
        include_unconfirmed: Incluye películas no confirmadas en Netflix España.
        page_size: Resultados por página.
        genre_id: Solo películas de este género de TMDB.

    Returns:
        La página pedida (vacía si el texto no tiene palabras).
    """
    page = max(1, page)
    query = build_fts_query(text)
    if query is None:
        return SearchPage([], 0, page, page_size)
    results, total = db.search_fts(
        query,
        only_netflix_es=not include_unconfirmed,
        limit=page_size,
        offset=(page - 1) * page_size,
        genre_id=genre_id,
    )
    return SearchPage(results, total, page, page_size)
