"""Traducciones al español de las frases y críticas en inglés.

Se guardan en ``data/traducciones.json`` junto al texto original, para que una nueva
descarga de pistas las reutilice mientras el original no cambie::

    {"81399614": {"original": "Colonel Bagley: …", "es": "Coronel Bagley: …"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from filmguess.catalog.db import CatalogDB

FILENAME = "traducciones.json"


class TranslationsError(Exception):
    """El fichero de traducciones no se puede leer."""


@dataclass(frozen=True, slots=True)
class Translation:
    """Un texto y su traducción."""

    original: str
    es: str


def load_translations(path: Path) -> dict[int, Translation]:
    """Lee el fichero de traducciones.

    Args:
        path: Ruta del JSON.

    Returns:
        Traducciones por ID de Netflix (vacío si el fichero no existe).

    Raises:
        TranslationsError: Si el fichero está dañado.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            int(key): Translation(str(item["original"]), str(item["es"]))
            for key, item in data.items()
            if str(item.get("es") or "").strip()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise TranslationsError(f"No se puede leer {path.name}: {exc}") from exc


def save_translations(path: Path, translations: dict[int, Translation]) -> None:
    """Guarda el fichero de traducciones (ordenado por ID).

    Args:
        path: Ruta del JSON.
        translations: Traducciones por ID de Netflix.
    """
    data = {
        str(key): {"original": t.original, "es": t.es} for key, t in sorted(translations.items())
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def translate(
    netflix_id: int, quote: str | None, lang: str | None, translations: dict[int, Translation]
) -> tuple[str | None, str | None]:
    """Devuelve la traducción guardada si corresponde a este mismo texto.

    Args:
        netflix_id: ID de Netflix.
        quote: Texto descargado.
        lang: Su idioma.
        translations: Traducciones guardadas.

    Returns:
        ``(texto, idioma)``: la traducción con ``es``, o el texto tal cual.
    """
    known = translations.get(netflix_id)
    if quote and lang != "es" and known and known.original == quote:
        return known.es, "es"
    return quote, lang


def apply_translations(db: CatalogDB, translations: dict[int, Translation]) -> int:
    """Pone en el catálogo las traducciones de los textos que aún están en inglés.

    Args:
        db: Catálogo.
        translations: Traducciones guardadas.

    Returns:
        Cuántos textos se han traducido.
    """
    applied = 0
    for netflix_id, quote in db.untranslated_quotes():
        text, lang = translate(netflix_id, quote, "en", translations)
        if lang == "es" and text:
            db.set_quote(netflix_id, text, "es")
            applied += 1
    return applied


def export_untranslated(db: CatalogDB, path: Path) -> int:
    """Escribe los textos pendientes de traducir como ``[{"id", "texto"}]``.

    Args:
        db: Catálogo.
        path: Ruta del JSON de salida.

    Returns:
        Cuántos textos se han exportado.
    """
    items = [{"id": netflix_id, "texto": quote} for netflix_id, quote in db.untranslated_quotes()]
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(items)
