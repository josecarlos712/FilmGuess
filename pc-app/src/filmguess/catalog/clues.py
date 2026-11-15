"""Pistas de cada película: géneros, reparto principal y una frase célebre o una crítica.

Solo se descargan para el catálogo de juego (películas disponibles en Netflix España):

1. TMDB: géneros en español, actor o actriz principal, su personaje y el eslogan.
2. Wikidata enlaza la película (por su ID de TMDB) con su página de Wikiquote, en español
   o en inglés; de ella sale la frase célebre.
3. Si no hay frase, un extracto de la primera crítica de TMDB; si tampoco, el eslogan.

Cualquier palabra del título que aparezca en una pista se tapa con «…».
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

QuoteSource = Literal["wikiquote", "critica", "eslogan"]

MASK = "…"
QUOTE_MIN = 25
QUOTE_MAX = 220
REVIEW_MAX = 240
TITLE_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "your",
        "their",
        "have",
        "what",
        "when",
        "where",
        "who",
        "los",
        "las",
        "del",
        "una",
        "unos",
        "unas",
        "con",
        "por",
        "para",
        "sin",
        "sobre",
        "entre",
        "como",
        "pero",
        "todo",
        "toda",
        "todos",
        "contra",
        "desde",
        "hasta",
        "tras",
    ],
)
"""Palabras del título que no se tapan solas (son demasiado comunes)."""

SKIP_SECTIONS = frozenset(
    {
        "cast",
        "reparto",
        "taglines",
        "tagline",
        "eslogan",
        "eslóganes",
        "external links",
        "enlaces externos",
        "see also",
        "véase también",
        "vease tambien",
        "about",
        "sobre",
        "quotes about",
        "referencias",
        "references",
        "notes",
        "notas",
    }
)


@dataclass(frozen=True, slots=True)
class Genre:
    """Un género de TMDB con su nombre en español."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Clues:
    """Pistas guardadas de una película."""

    genres: list[Genre] = field(default_factory=list)
    actor: str | None = None
    character: str | None = None
    quote: str | None = None
    quote_source: QuoteSource | None = None
    quote_lang: str | None = None


@dataclass(frozen=True, slots=True)
class TmdbClues:
    """Lo que aporta la ficha de TMDB."""

    genres: list[Genre]
    actor: str | None
    character: str | None
    tagline: str | None


def parse_tmdb_clues(details: dict[str, Any]) -> TmdbClues:
    """Saca géneros, reparto principal y eslogan de ``/movie/{id}?append_to_response=credits``.

    Args:
        details: JSON de la ficha.

    Returns:
        Las pistas de TMDB.
    """
    genres = [
        Genre(int(g["id"]), str(g["name"]))
        for g in details.get("genres") or []
        if isinstance(g, dict) and "id" in g and g.get("name")
    ]
    cast = (details.get("credits") or {}).get("cast") or []
    lead = min(
        (c for c in cast if isinstance(c, dict) and c.get("name")),
        key=lambda c: c.get("order", 999),
        default=None,
    )
    actor = str(lead["name"]).strip() if lead else None
    character = _clean_character(lead.get("character")) if lead else None
    tagline = " ".join(str(details.get("tagline") or "").split()) or None
    return TmdbClues(genres, actor, character, tagline)


def _clean_character(raw: object) -> str | None:
    text = " ".join(str(raw or "").split())
    # «Cobb / Dom» o «Joe (voice)»: se queda el primer nombre, sin anotaciones.
    text = re.sub(r"\s*\((?:voice|voz|uncredited|sin acreditar)[^)]*\)", "", text, flags=re.I)
    text = text.split(" / ")[0].strip()
    return text or None


# --- Tapar el título ---------------------------------------------------------------


def _fold(text: str) -> str:
    """Minúsculas y sin acentos, carácter a carácter (misma longitud que ``text``)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0]
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


def title_words(titles: Iterable[str | None]) -> list[str]:
    """Títulos completos y palabras distintivas que hay que tapar, en minúsculas sin acentos.

    Args:
        titles: Títulos de la película (español, original, CSV).

    Returns:
        Expresiones a tapar, de la más larga a la más corta.
    """
    found: set[str] = set()
    for title in titles:
        if not title:
            continue
        folded = " ".join(_fold(title).split())
        if len(folded) >= 3:
            found.add(folded)
        for word in re.findall(r"[^\W_]+", folded):
            if len(word) >= 4 and word not in TITLE_STOPWORDS and not word.isdigit():
                found.add(word)
    return sorted(found, key=len, reverse=True)


def redact(text: str, words: Sequence[str]) -> str:
    """Tapa con «…» las apariciones del título (sin distinguir acentos).

    Un título de varias palabras se tapa en minúsculas o mayúsculas; una palabra suelta
    solo si en el texto empieza por mayúscula (un nombre propio), para no tapar palabras
    corrientes como el «will» de *Will Hunting*.

    Args:
        text: Texto de la pista.
        words: Resultado de :func:`title_words`.

    Returns:
        El texto con el título tapado.
    """
    if not words:
        return text
    folded = _fold(text)
    pattern = re.compile(r"(?<![^\W_])(?:" + "|".join(map(re.escape, words)) + r")(?![^\W_])")
    spans = [
        m.span()
        for m in pattern.finditer(folded)
        if " " in m.group(0) or text[m.start()].isupper() or text[m.start()].isdigit()
    ]
    if not spans:
        return text
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(MASK)
        last = end
    out.append(text[last:])
    return re.sub(rf"{MASK}(?:\s*{MASK})+", MASK, "".join(out))


def is_mostly_masked(text: str) -> bool:
    """Indica si tras tapar el título apenas queda texto.

    Args:
        text: Texto ya tapado.

    Returns:
        ``True`` si no queda ninguna letra.
    """
    return not re.search(r"[^\W\d_]", text.replace(MASK, ""))


# --- Wikiquote ---------------------------------------------------------------------


def clean_wikitext(line: str) -> str:
    """Quita el marcado de MediaWiki de una línea de cita.

    Args:
        line: Línea de wikitexto (sin el ``*`` inicial).

    Returns:
        Texto plano.
    """
    text = re.sub(r"<!--.*?-->", "", line)
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    for _ in range(3):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:File|Archivo|Image|Imagen):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"\[[^\]]*\]", "", text)  # acotaciones: [se ríe]
    text = html.unescape(text)
    return " ".join(text.split()).strip(" :-" + chr(0x2013) + chr(0x2014))


def extract_quote(wikitext: str, words: Sequence[str]) -> str | None:
    """Elige la primera cita de una página de Wikiquote que sirve como pista.

    Solo cuentan las viñetas de primer nivel (``* …``) de las secciones de personajes o
    de citas; se saltan el reparto, los eslóganes y los enlaces.

    Args:
        wikitext: Wikitexto de la página.
        words: Palabras del título a tapar.

    Returns:
        La cita, ya tapada, o ``None``.
    """
    section: str | None = None
    for raw in wikitext.splitlines():
        line = raw.strip()
        heading = re.fullmatch(r"(=+)\s*(.*?)\s*\1", line)
        if heading:
            section = clean_wikitext(heading.group(2)).lower()
            continue
        if section is None or section in SKIP_SECTIONS:
            continue
        if not line.startswith("*") or line.startswith("**") or line.startswith("*:"):
            continue
        quote = clean_wikitext(line[1:])
        if not QUOTE_MIN <= len(quote) <= QUOTE_MAX:
            continue
        masked = redact(quote, words)
        if not is_mostly_masked(masked):
            return masked
    return None


# --- Críticas ----------------------------------------------------------------------


def review_excerpt(reviews: dict[str, Any], words: Sequence[str]) -> str | None:
    """Extracto de la primera crítica de TMDB, cortado en una frase.

    Args:
        reviews: JSON de ``/movie/{id}/reviews``.
        words: Palabras del título a tapar.

    Returns:
        El extracto, ya tapado, o ``None``.
    """
    for review in reviews.get("results") or []:
        if not isinstance(review, dict):
            continue
        text = clean_wikitext(
            str(review.get("content") or "").replace("\r", " ").replace("\n", " ")
        )
        text = re.sub(r"[*_#>]+", "", text).strip()
        if len(text) < QUOTE_MIN:
            continue
        excerpt = _cut(text, REVIEW_MAX)
        masked = redact(excerpt, words)
        if not is_mostly_masked(masked):
            return masked
    return None


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if end >= QUOTE_MIN:
        return head[: end + 1]
    return head.rsplit(" ", 1)[0].rstrip(",;:") + "…"
