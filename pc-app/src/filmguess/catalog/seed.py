"""Catálogo incluido con la app: se exporta una vez y se instala al primer arranque.

Así alguien nuevo puede jugar sin clave de TMDB ni importación: la app trae
``catalogo/catalogo-base.sqlite.gz`` (películas, pistas y búsqueda ya listas) y lo
descomprime en ``data/catalogo.sqlite`` si todavía no hay catálogo.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parents[3] / "catalogo" / "catalogo-base.sqlite.gz"
"""Ruta del catálogo incluido (``pc-app/catalogo/catalogo-base.sqlite.gz``)."""

PRIVATE_TABLES = ("random_history",)
"""Tablas con datos de quien juega; se vacían al exportar."""


class SeedError(Exception):
    """El catálogo no se puede exportar o instalar."""


@dataclass(frozen=True, slots=True)
class SeedSummary:
    """Resultado de exportar el catálogo."""

    path: Path
    movies: int
    clues: int
    size_bytes: int


def export_seed(source: Path, dest: Path, *, keep_imdb: bool = False) -> SeedSummary:
    """Copia el catálogo sin datos privados y lo guarda comprimido.

    Args:
        source: Catálogo de origen (``data/catalogo.sqlite``).
        dest: Fichero ``.sqlite.gz`` de destino.
        keep_imdb: Conserva la nota y los votos de IMDb. Por defecto se quitan, porque
            sus datos son de uso personal y no se pueden repartir; el juego usa
            entonces los de TMDB.

    Returns:
        Recuentos y tamaño del fichero.

    Raises:
        SeedError: Si el origen no existe o no es un catálogo.
    """
    if not source.exists():
        raise SeedError(f"No existe el catálogo {source}.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "catalogo.sqlite"
        try:
            _backup(source, copy)
            conn = sqlite3.connect(copy)
            try:
                with conn:
                    for table in PRIVATE_TABLES:
                        conn.execute(f"DELETE FROM {table}")
                    conn.execute("DELETE FROM sqlite_sequence")
                    if not keep_imdb:
                        conn.execute("UPDATE movies SET imdb_rating = NULL, imdb_votes = NULL")
                movies = conn.execute("SELECT count(*) FROM movies").fetchone()[0]
                clues = conn.execute("SELECT count(*) FROM clues").fetchone()[0]
                conn.execute("PRAGMA journal_mode = DELETE")
                conn.execute("VACUUM")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise SeedError(f"No se pudo copiar el catálogo: {exc}") from exc
        with copy.open("rb") as src, gzip.open(dest, "wb", compresslevel=9) as out:
            shutil.copyfileobj(src, out)
    return SeedSummary(dest, movies, clues, dest.stat().st_size)


def install_seed(db_file: Path, seed: Path = SEED_PATH) -> bool:
    """Instala el catálogo incluido si todavía no hay uno con películas.

    Args:
        db_file: Ruta del catálogo de la app (``data/catalogo.sqlite``).
        seed: Catálogo incluido comprimido.

    Returns:
        ``True`` si se ha instalado; ``False`` si ya había catálogo o no se incluye ninguno.

    Raises:
        SeedError: Si el catálogo incluido está dañado.
    """
    if not seed.exists() or _has_movies(db_file):
        return False
    db_file.parent.mkdir(parents=True, exist_ok=True)
    partial = db_file.with_name(db_file.name + ".nuevo")
    try:
        with gzip.open(seed, "rb") as src, partial.open("wb") as out:
            shutil.copyfileobj(src, out)
        conn = sqlite3.connect(f"file:{partial.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("SELECT count(*) FROM movies").fetchone()
        finally:
            conn.close()
    except (OSError, EOFError, sqlite3.Error) as exc:
        partial.unlink(missing_ok=True)
        raise SeedError(f"El catálogo incluido ({seed.name}) está dañado: {exc}") from exc
    for leftover in (db_file, _sidecar(db_file, "-wal"), _sidecar(db_file, "-shm")):
        leftover.unlink(missing_ok=True)
    partial.replace(db_file)
    return True


def _backup(source: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def _has_movies(db_file: Path) -> bool:
    if not db_file.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
        try:
            count: int = conn.execute("SELECT count(*) FROM movies").fetchone()[0]
            return count > 0
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        # Sin tabla de películas es un catálogo vacío; cualquier otro fallo (bloqueado,
        # sin permisos...) se trata como catálogo existente para no pisarlo nunca.
        return "no such table" not in str(exc)


def _sidecar(db_file: Path, suffix: str) -> Path:
    return db_file.with_name(db_file.name + suffix)
