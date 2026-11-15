"""Traducciones al español de frases y críticas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filmguess.catalog.clues import Clues
from filmguess.catalog.db import CatalogDB
from filmguess.catalog.translations import (
    Translation,
    TranslationsError,
    apply_translations,
    export_untranslated,
    load_translations,
    save_translations,
    translate,
)
from filmguess.game.hints import build_hints
from tests.test_clues import ORIGEN

ORIGINAL = "Cobb: An idea is like a virus."
SPANISH = "Cobb: Una idea es como un virus."


@pytest.fixture
def db() -> CatalogDB:
    catalog = CatalogDB(":memory:")
    catalog.save_clues(1, Clues(quote=ORIGINAL, quote_source="wikiquote", quote_lang="en"))
    catalog.save_clues(2, Clues(quote="Tu mente es la escena del crimen", quote_lang="es"))
    return catalog


def test_guardar_y_leer(tmp_path: Path) -> None:
    path = tmp_path / "traducciones.json"
    save_translations(path, {1: Translation(ORIGINAL, SPANISH)})

    assert load_translations(path) == {1: Translation(ORIGINAL, SPANISH)}
    assert json.loads(path.read_text(encoding="utf-8"))["1"]["es"] == SPANISH


def test_sin_fichero_no_hay_traducciones(tmp_path: Path) -> None:
    assert load_translations(tmp_path / "no.json") == {}


def test_fichero_danado(tmp_path: Path) -> None:
    path = tmp_path / "traducciones.json"
    path.write_text("{roto", encoding="utf-8")

    with pytest.raises(TranslationsError):
        load_translations(path)


def test_solo_se_traduce_el_mismo_original() -> None:
    known = {1: Translation(ORIGINAL, SPANISH)}

    assert translate(1, ORIGINAL, "en", known) == (SPANISH, "es")
    assert translate(1, "Otro texto", "en", known) == ("Otro texto", "en")
    assert translate(2, ORIGINAL, "en", known) == (ORIGINAL, "en")


def test_aplicar_y_exportar(db: CatalogDB, tmp_path: Path) -> None:
    out = tmp_path / "pendientes.json"
    assert export_untranslated(db, out) == 1
    assert json.loads(out.read_text(encoding="utf-8")) == [{"id": 1, "texto": ORIGINAL}]

    assert apply_translations(db, {1: Translation(ORIGINAL, SPANISH)}) == 1

    clues = db.clues(1)
    assert clues is not None and (clues.quote, clues.quote_lang) == (SPANISH, "es")
    assert db.untranslated_quotes() == []


def test_frase_traducida_sin_aviso_de_ingles(db: CatalogDB) -> None:
    apply_translations(db, {1: Translation(ORIGINAL, SPANISH)})

    hint = build_hints(ORIGEN, db.clues(1), category=False)[-1]

    assert (hint.label, hint.text) == ("Una frase de la película", SPANISH)
