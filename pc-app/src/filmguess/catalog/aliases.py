"""Títulos alternativos de las películas, para aceptar lo que escriben los jugadores.

Hay dos fuentes:

- **TMDB** (``arrancar.bat alias``): títulos de otros países de habla hispana o inglesa
  (por ejemplo «Interestelar», el de México) y las traducciones al español y al inglés.
  Se guardan en la tabla ``aliases`` del catálogo, así que viajan con el catálogo incluido.
- **Lista manual** (``pc-app/catalogo/alias.json``): apodos y formas de escribir que no
  vienen en ningún sitio. Formato ``{"<id de Netflix>": ["alias", ...]}``; las claves que
  empiezan por ``_`` son comentarios.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filmguess.catalog.db import CatalogDB, ClueTarget
from filmguess.catalog.normalize import strip_accents
from filmguess.catalog.tmdb import TmdbClient, TmdbError, TmdbNotFoundError

MANUAL_ALIASES_PATH = Path(__file__).resolve().parents[3] / "catalogo" / "alias.json"

COUNTRIES = frozenset({"ES", "MX", "AR", "CO", "CL", "PE", "VE", "UY", "US", "GB"})
"""Países cuyos títulos alternativos se aceptan."""
LANGUAGES = frozenset({"es", "en"})
"""Idiomas cuyas traducciones del título se aceptan."""
MAX_LENGTH = 120


class AliasError(Exception):
    """La lista manual de alias no se puede leer."""


def is_latin(text: str) -> bool:
    """Si el texto se puede escribir con un teclado español (sin otros alfabetos).

    Args:
        text: Título.

    Returns:
        ``True`` si, quitando tildes, solo tiene caracteres ASCII.
    """
    return strip_accents(text).replace("ñ", "n").replace("Ñ", "N").isascii()


def parse_tmdb_aliases(data: dict[str, Any]) -> list[str]:
    """Títulos alternativos útiles de una respuesta de TMDB.

    Args:
        data: JSON de ``/movie/{id}`` con ``alternative_titles`` y ``translations``.

    Returns:
        Títulos sin repetir, en el orden en que aparecen.
    """
    found: list[str] = []
    for item in (data.get("alternative_titles") or {}).get("titles") or []:
        if item.get("iso_3166_1") in COUNTRIES:
            found.append(str(item.get("title") or ""))
    for item in (data.get("translations") or {}).get("translations") or []:
        if item.get("iso_639_1") in LANGUAGES:
            found.append(str((item.get("data") or {}).get("title") or ""))
    clean = (" ".join(t.split()) for t in found)
    return list(dict.fromkeys(t for t in clean if t and len(t) <= MAX_LENGTH and is_latin(t)))


def load_manual_aliases(path: Path = MANUAL_ALIASES_PATH) -> dict[int, list[str]]:
    """Lee la lista manual de alias.

    Args:
        path: Fichero JSON.

    Returns:
        Alias por ID de Netflix; vacío si el fichero no existe.

    Raises:
        AliasError: Si el fichero no es un JSON con el formato esperado.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AliasError(f"No se puede leer {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AliasError(f'{path.name} debe ser un objeto {{"id": ["alias", ...]}}.')
    result: dict[int, list[str]] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if not key.isdigit() or not isinstance(value, list):
            raise AliasError(f"{path.name}: «{key}» debe ser un ID de Netflix con una lista.")
        result[int(key)] = [str(v) for v in value if str(v).strip()]
    return result


@dataclass(slots=True)
class AliasProgress:
    """Progreso de la descarga de títulos alternativos."""

    total: int
    done: int = 0
    titles: int = 0
    errors: int = 0


AliasCallback = Callable[[AliasProgress], None]


async def fetch_aliases(
    db: CatalogDB,
    client: TmdbClient,
    targets: Sequence[ClueTarget],
    on_progress: AliasCallback | None = None,
    concurrency: int = 8,
) -> AliasProgress:
    """Descarga de TMDB los títulos alternativos de las películas y los guarda.

    Args:
        db: Catálogo.
        client: Cliente de TMDB.
        targets: Películas (del catálogo de juego).
        on_progress: Recibe el progreso tras cada película.
        concurrency: Peticiones simultáneas.

    Returns:
        El resumen.

    Raises:
        TmdbError: Si TMDB rechaza la clave.
    """
    progress = AliasProgress(total=len(targets))
    gate = asyncio.Semaphore(concurrency)

    async def one(target: ClueTarget) -> None:
        async with gate:
            try:
                titles = parse_tmdb_aliases(await client.movie_aliases(target.tmdb_id))
            except TmdbNotFoundError:
                titles = []
            except TmdbError as exc:
                if "clave" in str(exc):
                    raise
                progress.errors += 1
                progress.done += 1
                return
        db.save_aliases(target.netflix_id, titles)
        progress.titles += len(titles)
        progress.done += 1
        if on_progress is not None:
            on_progress(progress)

    await asyncio.gather(*(one(t) for t in targets))
    return progress
