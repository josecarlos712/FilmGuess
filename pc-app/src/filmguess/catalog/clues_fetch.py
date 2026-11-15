"""Descarga de las pistas del catálogo de juego (TMDB, Wikidata y Wikiquote)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import httpx

from filmguess.catalog.clues import (
    Clues,
    QuoteSource,
    TmdbClues,
    extract_quote,
    parse_tmdb_clues,
    redact,
    review_excerpt,
    title_words,
)
from filmguess.catalog.db import CatalogDB, ClueTarget
from filmguess.catalog.tmdb import TmdbAuthError, TmdbClient, TmdbError
from filmguess.catalog.translations import Translation, translate
from filmguess.catalog.wikidata import SPARQL_URL, USER_AGENT

log = logging.getLogger(__name__)

WIKIQUOTE_LANGS = ("es", "en")
"""Wikiquote en español antes que en inglés."""
SPARQL_BATCH = 200
WIKIQUOTE_BATCH = 20
CONCURRENCY = 8


@dataclass(slots=True)
class CluesProgress:
    """Progreso de la descarga de pistas."""

    stage: str = "tmdb"
    total: int = 0
    done: int = 0
    errors: int = 0
    quotes: int = 0
    reviews: int = 0
    taglines: int = 0


ProgressCallback = Callable[[CluesProgress], None]


def _sparql_query(tmdb_ids: Sequence[int]) -> str:
    values = " ".join(f'"{i}"' for i in tmdb_ids)
    sites = ", ".join(f"<https://{lang}.wikiquote.org/>" for lang in WIKIQUOTE_LANGS)
    return (
        "SELECT ?tmdb ?page WHERE { VALUES ?tmdb { " + values + " } "
        "?item wdt:P4947 ?tmdb . ?page schema:about ?item ; schema:isPartOf ?site . "
        f"FILTER(?site IN ({sites})) }}"
    )


def parse_sparql_pages(data: dict[str, Any]) -> dict[int, dict[str, str]]:
    """Convierte la respuesta SPARQL en páginas de Wikiquote por ID de TMDB.

    Args:
        data: JSON de resultados SPARQL (``tmdb`` y ``page``).

    Returns:
        ``{tmdb_id: {idioma: título de la página}}``.
    """
    pages: dict[int, dict[str, str]] = {}
    for row in data.get("results", {}).get("bindings", []):
        tmdb = str(row.get("tmdb", {}).get("value", ""))
        url = str(row.get("page", {}).get("value", ""))
        if not tmdb.isdigit() or ".wikiquote.org/wiki/" not in url:
            continue
        lang = url.split("//", 1)[1].split(".", 1)[0]
        title = unquote(url.split("/wiki/", 1)[1]).replace("_", " ")
        pages.setdefault(int(tmdb), {})[lang] = title
    return pages


def parse_wikiquote_pages(data: dict[str, Any]) -> dict[str, str]:
    """Wikitexto de cada página pedida, siguiendo normalizaciones y redirecciones.

    Args:
        data: JSON de ``action=query&prop=revisions`` (``formatversion=2``).

    Returns:
        ``{título pedido: wikitexto}``.
    """
    query = data.get("query") or {}
    alias: dict[str, str] = {}
    for item in (query.get("normalized") or []) + (query.get("redirects") or []):
        alias[str(item.get("from"))] = str(item.get("to"))
    texts: dict[str, str] = {}
    for page in query.get("pages") or []:
        revisions = page.get("revisions") or []
        if not revisions:
            continue
        slot = (revisions[0].get("slots") or {}).get("main") or {}
        content = slot.get("content")
        if isinstance(content, str):
            texts[str(page.get("title"))] = content
    resolved: dict[str, str] = {}
    for name in set(alias) | set(texts):
        target = name
        for _ in range(3):
            if target in texts:
                break
            target = alias.get(target, target)
        if target in texts:
            resolved[name] = texts[target]
    return resolved


class WikiClient:
    """Consultas a Wikidata y Wikiquote (sin clave)."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Crea el cliente.

        Args:
            transport: Transporte alternativo (para pruebas).
        """
        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=60, transport=transport
        )

    async def aclose(self) -> None:
        """Cierra las conexiones."""
        await self._http.aclose()

    async def wikiquote_pages(self, tmdb_ids: Sequence[int]) -> dict[int, dict[str, str]]:
        """Páginas de Wikiquote enlazadas en Wikidata con cada ID de TMDB.

        Args:
            tmdb_ids: IDs de TMDB.

        Returns:
            ``{tmdb_id: {idioma: título}}``; un lote que falla se omite.
        """
        pages: dict[int, dict[str, str]] = {}
        for start in range(0, len(tmdb_ids), SPARQL_BATCH):
            batch = tmdb_ids[start : start + SPARQL_BATCH]
            try:
                response = await self._http.post(
                    SPARQL_URL,
                    data={"query": _sparql_query(batch)},
                    headers={"Accept": "application/sparql-results+json"},
                )
                response.raise_for_status()
                pages.update(parse_sparql_pages(response.json()))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Wikidata falló en un lote de %d películas: %s", len(batch), exc)
        return pages

    async def wikitexts(self, lang: str, titles: Sequence[str]) -> dict[str, str]:
        """Wikitexto de varias páginas de Wikiquote.

        Args:
            lang: Idioma de Wikiquote (``es``, ``en``).
            titles: Títulos de las páginas.

        Returns:
            ``{título: wikitexto}``; un lote que falla se omite.
        """
        texts: dict[str, str] = {}
        url = f"https://{lang}.wikiquote.org/w/api.php"
        for start in range(0, len(titles), WIKIQUOTE_BATCH):
            batch = titles[start : start + WIKIQUOTE_BATCH]
            params = {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
                "titles": "|".join(batch),
            }
            try:
                response = await self._http.get(url, params=params)
                response.raise_for_status()
                texts.update(parse_wikiquote_pages(response.json()))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Wikiquote (%s) falló en un lote: %s", lang, exc)
        return texts


async def fetch_clues(
    db: CatalogDB,
    tmdb: TmdbClient,
    wiki: WikiClient,
    *,
    refresh: bool = False,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
    translations: dict[int, Translation] | None = None,
) -> CluesProgress:
    """Descarga y guarda las pistas del catálogo de juego.

    Args:
        db: Catálogo.
        tmdb: Cliente de TMDB.
        wiki: Cliente de Wikidata y Wikiquote.
        refresh: Vuelve a descargar también las que ya tienen pistas.
        limit: Máximo de películas.
        on_progress: Recibe el progreso.
        translations: Traducciones guardadas de frases y críticas.

    Returns:
        El resumen.

    Raises:
        TmdbAuthError: Si TMDB rechaza la clave.
    """
    targets = db.clue_targets(refresh=refresh)[:limit]
    progress = CluesProgress(total=len(targets))
    report = on_progress or (lambda _p: None)
    report(progress)

    # 1. TMDB: géneros, reparto y eslogan.
    details: dict[int, TmdbClues] = {}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(target: ClueTarget) -> None:
        async with semaphore:
            try:
                details[target.netflix_id] = parse_tmdb_clues(
                    await tmdb.movie_clues(target.tmdb_id)
                )
            except TmdbAuthError:
                raise
            except TmdbError as exc:
                progress.errors += 1
                log.warning("TMDB falló en %s: %s", target.netflix_id, exc)
            progress.done += 1
            report(progress)

    await asyncio.gather(*(one(t) for t in targets))

    # 2. Wikidata y Wikiquote: frase célebre.
    progress.stage = "wikiquote"
    report(progress)
    ok = [t for t in targets if t.netflix_id in details]
    pages = await wiki.wikiquote_pages([t.tmdb_id for t in ok])
    texts: dict[str, dict[str, str]] = {}
    for wiki_lang in WIKIQUOTE_LANGS:
        wanted = sorted({p[wiki_lang] for p in pages.values() if wiki_lang in p})
        texts[wiki_lang] = await wiki.wikitexts(wiki_lang, wanted) if wanted else {}

    # 3. Críticas de TMDB para las que no tienen frase, y guardado.
    progress.stage = "criticas"
    report(progress)
    for target in ok:
        words = title_words(target.titles)
        info = details[target.netflix_id]
        quote: str | None = None
        source: QuoteSource | None = None
        lang: str | None = None
        for candidate in WIKIQUOTE_LANGS:
            title = pages.get(target.tmdb_id, {}).get(candidate)
            wikitext = texts[candidate].get(title) if title else None
            quote = extract_quote(wikitext, words) if wikitext else None
            if quote:
                source, lang = "wikiquote", candidate
                progress.quotes += 1
                break
        if quote is None:
            try:
                quote = review_excerpt(await tmdb.movie_reviews(target.tmdb_id), words)
            except TmdbAuthError:
                raise
            except TmdbError as exc:
                log.warning("Críticas de TMDB fallaron en %s: %s", target.netflix_id, exc)
            if quote:
                source, lang = "critica", "en"
                progress.reviews += 1
        if quote is None and info.tagline:
            quote, source, lang = redact(info.tagline, words), "eslogan", "es"
            progress.taglines += 1
        quote, lang = translate(target.netflix_id, quote, lang, translations or {})
        character = redact(info.character, words) if info.character else None
        db.save_clues(
            target.netflix_id,
            Clues(info.genres, info.actor, character, quote, source, lang),
        )
    progress.stage = "hecho"
    report(progress)
    return progress
