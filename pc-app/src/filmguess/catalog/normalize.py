"""Normalización y comparación de títulos de películas."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_LEADING_ARTICLE = re.compile(r"^(the|a|an|el|la|los|las|un|una)\s+")


def strip_accents(text: str) -> str:
    """Quita tildes y diacríticos (``"Orígen"`` → ``"Origen"``).

    Args:
        text: Texto de entrada.

    Returns:
        El texto sin marcas diacríticas.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    """Normaliza un título para compararlo: minúsculas, sin acentos ni signos.

    ``&`` se convierte en ``and`` y se quita el artículo inicial.

    Args:
        title: Título original.

    Returns:
        Título normalizado con palabras separadas por un espacio.
    """
    text = strip_accents(title).lower().replace("&", " and ")
    text = _NON_ALNUM.sub(" ", text).strip()
    return _LEADING_ARTICLE.sub("", text)


def tokenize(text: str) -> list[str]:
    """Divide un texto en palabras normalizadas (sin quitar artículos).

    Args:
        text: Texto libre, por ejemplo lo que escribe el usuario en el buscador.

    Returns:
        Lista de palabras en minúsculas, sin acentos ni signos.
    """
    return _NON_ALNUM.sub(" ", strip_accents(text).lower()).split()


def title_similarity(a: str, b: str) -> float:
    """Similitud entre dos títulos (0 a 1) tras normalizarlos.

    Args:
        a: Primer título.
        b: Segundo título.

    Returns:
        1.0 si coinciden al normalizar; si no, la razón de ``SequenceMatcher``.
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
