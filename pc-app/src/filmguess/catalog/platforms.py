"""Catálogo multiplataforma en bruto: qué películas hay en cada plataforma de un país.

Se guarda en una base de datos aparte del catálogo del juego para poder estudiar las
plataformas una a una. Fuentes:

* TMDB ``/discover/movie``: enumera las películas de suscripción, gratis o con anuncios
  de cada plataforma en la región.
* TMDB ``/movie/{id}/watch/providers``: disponibilidad exacta de cada película en todas
  las plataformas (también alquiler y compra).
* Wikidata: IDs propios de cada plataforma (para abrir la película directamente).
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filmguess.catalog.imdb import iter_ratings
from filmguess.catalog.tmdb import TmdbAuthError, TmdbClient, TmdbError, TmdbNotFoundError
from filmguess.catalog.wikidata import WikidataError, download_sparql_csv

log = logging.getLogger(__name__)

MAX_PAGES = 500
"""TMDB no sirve más allá de la página 500 de ``/discover``."""
SUBSCRIPTION_KINDS = ("flatrate", "free", "ads")
ALL_KINDS = (*SUBSCRIPTION_KINDS, "rent", "buy")
FIRST_YEAR = 1870

WIKIDATA_PLATFORMS: dict[str, str] = {
    "netflix": "P1874",
    "prime_video": "P8055",
    "prime_video_id": "P14440",
    "prime_video_gti": "P14462",
    "disney_plus": "P7595",
    "apple_tv": "P9586",
    "hbo_max": "P8298",
    "paramount_plus": "P13147",
    "mubi": "P7299",
    "itunes": "P6398",
    "google_play": "P6562",
    "hulu": "P6466",
}
"""Plataforma → propiedad de Wikidata con su ID de película."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    provider_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    priority    INTEGER
);
CREATE TABLE IF NOT EXISTS titles (
    tmdb_id        INTEGER PRIMARY KEY,
    title          TEXT,
    original_title TEXT,
    year           INTEGER,
    popularity     REAL,
    vote_avg       REAL,
    vote_count     INTEGER,
    poster_path    TEXT,
    checked_at     TEXT,
    imdb_id        TEXT,
    imdb_rating    REAL,
    imdb_votes     INTEGER,
    ids_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS availability (
    tmdb_id     INTEGER NOT NULL,
    provider_id INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    PRIMARY KEY (tmdb_id, provider_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_availability_provider ON availability(provider_id, kind);
CREATE TABLE IF NOT EXISTS external_ids (
    platform    TEXT NOT NULL,
    external_id TEXT NOT NULL,
    tmdb_id     INTEGER,
    imdb_id     TEXT,
    PRIMARY KEY (platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_external_tmdb ON external_ids(tmdb_id);
"""


@dataclass(slots=True)
class HarvestProgress:
    """Progreso de una descarga (se notifica a menudo)."""

    stage: str
    done: int = 0
    total: int = 0
    errors: int = 0


@dataclass(slots=True)
class HarvestSummary:
    """Resultado de :func:`harvest`."""

    providers: int = 0
    discovered: int = 0
    checked: int = 0
    check_errors: int = 0
    external_ids: dict[str, int] = field(default_factory=dict)
    wikidata_errors: dict[str, str] = field(default_factory=dict)


ProgressCallback = Callable[[HarvestProgress], None]


class PlatformsDB:
    """Base de datos del catálogo multiplataforma."""

    def __init__(self, path: Path | str) -> None:
        """Abre (o crea) la base de datos.

        Args:
            path: Ruta del fichero SQLite, o ``":memory:"`` para pruebas.
        """
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        """Cierra la conexión."""
        self._conn.close()

    def _migrate(self) -> None:
        columns = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(titles)")}
        added = {
            "imdb_id": "TEXT",
            "imdb_rating": "REAL",
            "imdb_votes": "INTEGER",
            "ids_checked_at": "TEXT",
        }
        with self._conn:
            for name, kind in added.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE titles ADD COLUMN {name} {kind}")
            # Sin este índice, aplicar las valoraciones recorre la tabla por cada película.
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_imdb ON titles(imdb_id)")

    def save_providers(self, providers: Iterable[dict[str, Any]], region: str) -> int:
        """Guarda las plataformas de la región.

        Args:
            providers: Elementos de ``/watch/providers/movie``.
            region: País, para leer su prioridad de visualización.

        Returns:
            Número de plataformas guardadas.
        """
        rows = [
            (
                int(p["provider_id"]),
                str(p.get("provider_name") or ""),
                (p.get("display_priorities") or {}).get(region, p.get("display_priority")),
            )
            for p in providers
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT INTO providers(provider_id, name, priority) VALUES (?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET name=excluded.name, "
                "priority=excluded.priority",
                rows,
            )
        return len(rows)

    def provider_ids(self) -> list[int]:
        """IDs de las plataformas guardadas, por prioridad.

        Returns:
            Lista de IDs.
        """
        sql = "SELECT provider_id FROM providers ORDER BY priority IS NULL, priority, provider_id"
        return [int(r[0]) for r in self._conn.execute(sql)]

    def upsert_titles(self, results: Iterable[dict[str, Any]]) -> None:
        """Guarda o actualiza los datos básicos de películas de ``/discover``.

        No toca ``checked_at``: la disponibilidad se comprueba aparte.

        Args:
            results: Elementos de ``results`` de ``/discover/movie``.
        """
        rows = []
        for r in results:
            if "id" not in r:
                continue
            release = str(r.get("release_date") or "")
            rows.append(
                (
                    int(r["id"]),
                    r.get("title") or None,
                    r.get("original_title") or None,
                    int(release[:4]) if release[:4].isdigit() else None,
                    r.get("popularity"),
                    r.get("vote_average"),
                    r.get("vote_count"),
                    r.get("poster_path") or None,
                )
            )
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO titles(tmdb_id, title, original_title, year, popularity, vote_avg,
                    vote_count, poster_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tmdb_id) DO UPDATE SET title=excluded.title,
                    original_title=excluded.original_title, year=excluded.year,
                    popularity=excluded.popularity, vote_avg=excluded.vote_avg,
                    vote_count=excluded.vote_count, poster_path=excluded.poster_path
                """,
                rows,
            )

    def unchecked(self, limit: int | None = None) -> list[int]:
        """Películas cuya disponibilidad aún no se ha comprobado, las más populares primero.

        Args:
            limit: Máximo de filas; ``None`` para todas.

        Returns:
            IDs de TMDB.
        """
        sql = "SELECT tmdb_id FROM titles WHERE checked_at IS NULL ORDER BY popularity DESC"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [int(r[0]) for r in self._conn.execute(sql, params)]

    def reset_checks(self) -> None:
        """Marca todas las películas para volver a comprobar su disponibilidad."""
        with self._conn:
            self._conn.execute("UPDATE titles SET checked_at=NULL")

    def save_availability(self, tmdb_id: int, offers: dict[str, Any] | None) -> None:
        """Sustituye la disponibilidad de una película en la región.

        Args:
            tmdb_id: ID de TMDB.
            offers: Entrada de la región en ``watch/providers`` (``None`` si no hay ninguna).
        """
        rows = []
        for kind in ALL_KINDS:
            for offer in (offers or {}).get(kind, []) or []:
                if isinstance(offer, dict) and "provider_id" in offer:
                    rows.append((tmdb_id, int(offer["provider_id"]), kind))
        with self._conn:
            self._conn.execute("DELETE FROM availability WHERE tmdb_id=?", (tmdb_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO availability(tmdb_id, provider_id, kind) VALUES (?, ?, ?)",
                rows,
            )
            self._conn.execute("UPDATE titles SET checked_at=? WHERE tmdb_id=?", (_now(), tmdb_id))

    def replace_external_ids(
        self, platform: str, rows: Iterable[tuple[str, int | None, str | None]]
    ) -> int:
        """Sustituye los IDs propios de una plataforma.

        Args:
            platform: Nombre de la plataforma (clave de :data:`WIKIDATA_PLATFORMS`).
            rows: Tuplas ``(id_en_la_plataforma, tmdb_id, imdb_id)``.

        Returns:
            Número de IDs guardados.
        """
        with self._conn:
            self._conn.execute("DELETE FROM external_ids WHERE platform=?", (platform,))
            cur = self._conn.executemany(
                "INSERT OR IGNORE INTO external_ids(platform, external_id, tmdb_id, imdb_id) "
                "VALUES (?, ?, ?, ?)",
                ((platform, ext, tmdb, imdb) for ext, tmdb, imdb in rows),
            )
        return cur.rowcount

    def fill_imdb_from_external(self) -> int:
        """Copia el IMDb ID que da Wikidata a las películas que aún no lo tienen.

        Returns:
            Películas completadas.
        """
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE titles SET imdb_id = (
                    SELECT e.imdb_id FROM external_ids e
                    WHERE e.tmdb_id = titles.tmdb_id AND e.imdb_id IS NOT NULL LIMIT 1
                )
                WHERE imdb_id IS NULL AND EXISTS (
                    SELECT 1 FROM external_ids e
                    WHERE e.tmdb_id = titles.tmdb_id AND e.imdb_id IS NOT NULL
                )
                """
            )
        return cur.rowcount

    def without_imdb_id(self, limit: int | None = None) -> list[int]:
        """Películas sin IMDb ID que aún no se han consultado en TMDB.

        Args:
            limit: Máximo de filas; ``None`` para todas.

        Returns:
            IDs de TMDB, las más populares primero.
        """
        sql = (
            "SELECT tmdb_id FROM titles WHERE imdb_id IS NULL AND ids_checked_at IS NULL "
            "ORDER BY popularity DESC"
        )
        params: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [int(r[0]) for r in self._conn.execute(sql, params)]

    def set_imdb_id(self, tmdb_id: int, imdb_id: str | None) -> None:
        """Guarda el IMDb ID de una película y la marca como consultada.

        Args:
            tmdb_id: ID de TMDB.
            imdb_id: ID de IMDb (``tt...``), o ``None`` si TMDB no lo tiene.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE titles SET imdb_id=COALESCE(?, imdb_id), ids_checked_at=? WHERE tmdb_id=?",
                (imdb_id, _now(), tmdb_id),
            )

    def imdb_ids(self) -> set[str]:
        """IMDb IDs presentes.

        Returns:
            Conjunto de IDs.
        """
        rows = self._conn.execute("SELECT imdb_id FROM titles WHERE imdb_id IS NOT NULL")
        return {str(r[0]) for r in rows}

    def update_imdb_ratings(self, ratings: Iterable[tuple[str, float, int]]) -> int:
        """Actualiza nota y votos de IMDb.

        Args:
            ratings: Tuplas ``(imdb_id, nota, votos)``.

        Returns:
            Películas actualizadas.
        """
        with self._conn:
            cur = self._conn.executemany(
                "UPDATE titles SET imdb_rating=?, imdb_votes=? WHERE imdb_id=?",
                ((rating, votes, imdb_id) for imdb_id, rating, votes in ratings),
            )
        return cur.rowcount

    def export_rows(self, kinds: Iterable[str] = ALL_KINDS) -> list[tuple[Any, ...]]:
        """Una fila por película, plataforma y tipo de oferta.

        Args:
            kinds: Tipos de oferta a incluir.

        Returns:
            Tuplas en el orden de :data:`EXPORT_COLUMNS`; primero las más votadas en IMDb.
        """
        wanted = list(kinds)
        marks = ", ".join("?" for _ in wanted)
        sql = f"""
            SELECT t.tmdb_id, t.title, t.original_title, t.year, p.name, a.kind,
                t.imdb_id, t.imdb_rating, t.imdb_votes, t.vote_avg, t.vote_count,
                (SELECT GROUP_CONCAT(e.platform || ':' || e.external_id, ' ')
                 FROM external_ids e WHERE e.tmdb_id = t.tmdb_id)
            FROM availability a
            JOIN titles t USING(tmdb_id)
            JOIN providers p USING(provider_id)
            WHERE a.kind IN ({marks})
            ORDER BY t.imdb_votes IS NULL, t.imdb_votes DESC, t.tmdb_id, p.priority, a.kind
        """
        return [tuple(r) for r in self._conn.execute(sql, wanted)]

    def stats(self) -> list[tuple[str, str, int]]:
        """Películas por plataforma y tipo de oferta.

        Returns:
            Tuplas ``(plataforma, tipo, películas)`` de mayor a menor.
        """
        sql = (
            "SELECT p.name, a.kind, COUNT(*) n FROM availability a "
            "JOIN providers p USING(provider_id) GROUP BY p.name, a.kind ORDER BY n DESC"
        )
        return [(str(r[0]), str(r[1]), int(r[2])) for r in self._conn.execute(sql)]

    def count_titles(self) -> int:
        """Número de películas guardadas.

        Returns:
            Recuento.
        """
        return int(self._conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0])


async def discover_provider(
    db: PlatformsDB,
    client: TmdbClient,
    provider_id: int,
    *,
    kinds: Iterable[str] = SUBSCRIPTION_KINDS,
    last_year: int | None = None,
) -> int:
    """Enumera todas las películas de una plataforma y las guarda.

    Si una consulta pasa de 500 páginas, la divide por años de estreno.

    Args:
        db: Base de datos multiplataforma.
        client: Cliente de TMDB (su región e idioma se aplican a la consulta).
        provider_id: ID de la plataforma en TMDB.
        kinds: Tipos de oferta que cuentan.
        last_year: Último año de estreno al dividir; por defecto el actual + 2.

    Returns:
        Número de resultados recibidos (puede contar películas repetidas).

    Raises:
        TmdbError: Si TMDB falla.
    """
    base = {
        "with_watch_providers": str(provider_id),
        "with_watch_monetization_types": "|".join(kinds),
    }
    top = last_year if last_year is not None else datetime.now(UTC).year + 2

    async def fetch(params: dict[str, str], lo: int | None, hi: int | None) -> int:
        query = dict(params)
        if lo is not None and hi is not None:
            query["primary_release_date.gte"] = f"{lo}-01-01"
            query["primary_release_date.lte"] = f"{hi}-12-31"
        first = await client.discover_movies(query | {"page": "1"})
        total_pages = int(first.get("total_pages") or 0)
        if total_pages > MAX_PAGES:
            if lo is None or hi is None:
                return await fetch(params, FIRST_YEAR, top)
            if lo < hi:
                mid = (lo + hi) // 2
                return await fetch(params, lo, mid) + await fetch(params, mid + 1, hi)
            log.warning("Plataforma %d, año %d: más de %d páginas", provider_id, lo, MAX_PAGES)
        results = list(first.get("results", []))
        db.upsert_titles(results)
        count = len(results)
        for page in range(2, min(total_pages, MAX_PAGES) + 1):
            data = await client.discover_movies(query | {"page": str(page)})
            batch = list(data.get("results", []))
            db.upsert_titles(batch)
            count += len(batch)
        return count

    return await fetch(base, None, None)


async def check_availability(
    db: PlatformsDB,
    client: TmdbClient,
    *,
    limit: int | None = None,
    workers: int = 8,
    on_progress: ProgressCallback | None = None,
    max_consecutive_errors: int = 20,
) -> HarvestProgress:
    """Comprueba en TMDB la disponibilidad exacta de las películas pendientes.

    Se puede interrumpir y reanudar: cada película se guarda al terminarla.

    Args:
        db: Base de datos multiplataforma.
        client: Cliente de TMDB.
        limit: Máximo de películas en esta ejecución.
        workers: Peticiones simultáneas.
        on_progress: Se llama tras cada película.
        max_consecutive_errors: Aborta si TMDB falla tantas veces seguidas.

    Returns:
        El progreso final.

    Raises:
        TmdbError: Si la clave no es válida o hay demasiados errores seguidos.
    """

    async def handle(tmdb_id: int) -> None:
        try:
            data = await client.movie_watch_providers(tmdb_id)
        except TmdbNotFoundError:
            db.save_availability(tmdb_id, None)
            return
        db.save_availability(tmdb_id, (data.get("results") or {}).get(client.region))

    pending = db.unchecked(limit)
    return await _run_queue(
        pending,
        handle,
        HarvestProgress(stage="disponibilidad", total=len(pending)),
        workers=workers,
        on_progress=on_progress,
        max_consecutive_errors=max_consecutive_errors,
    )


async def fill_imdb_ids(
    db: PlatformsDB,
    client: TmdbClient,
    *,
    limit: int | None = None,
    workers: int = 8,
    on_progress: ProgressCallback | None = None,
    max_consecutive_errors: int = 20,
) -> HarvestProgress:
    """Completa el IMDb ID: primero con Wikidata y luego preguntando a TMDB.

    Se puede interrumpir y reanudar; cada película se consulta en TMDB una sola vez.

    Args:
        db: Base de datos multiplataforma.
        client: Cliente de TMDB.
        limit: Máximo de películas a consultar en TMDB.
        workers: Peticiones simultáneas.
        on_progress: Se llama tras cada película.
        max_consecutive_errors: Aborta si TMDB falla tantas veces seguidas.

    Returns:
        El progreso de las consultas a TMDB.

    Raises:
        TmdbError: Si la clave no es válida o hay demasiados errores seguidos.
    """
    db.fill_imdb_from_external()

    async def handle(tmdb_id: int) -> None:
        try:
            data = await client.movie_external_ids(tmdb_id)
        except TmdbNotFoundError:
            db.set_imdb_id(tmdb_id, None)
            return
        imdb = str(data.get("imdb_id") or "")
        db.set_imdb_id(tmdb_id, imdb if imdb.startswith("tt") else None)

    pending = db.without_imdb_id(limit)
    return await _run_queue(
        pending,
        handle,
        HarvestProgress(stage="imdb_ids", total=len(pending)),
        workers=workers,
        on_progress=on_progress,
        max_consecutive_errors=max_consecutive_errors,
    )


def apply_imdb_ratings(db: PlatformsDB, ratings_path: Path) -> int:
    """Aplica las valoraciones del fichero público de IMDb.

    Args:
        db: Base de datos multiplataforma.
        ratings_path: Ruta del ``title.ratings.tsv.gz``.

    Returns:
        Películas con valoración.

    Raises:
        ImdbError: Si el fichero no se puede leer.
    """
    return db.update_imdb_ratings(iter_ratings(ratings_path, db.imdb_ids()))


KIND_LABELS = {
    "flatrate": "suscripción",
    "free": "gratis",
    "ads": "con anuncios",
    "rent": "alquiler",
    "buy": "compra",
}
EXPORT_COLUMNS = (
    "tmdb_id",
    "titulo",
    "titulo_original",
    "anio",
    "plataforma",
    "tipo",
    "imdb_id",
    "nota_imdb",
    "votos_imdb",
    "nota_tmdb",
    "votos_tmdb",
    "ids_plataforma",
)


def export_csv(
    db: PlatformsDB,
    dest: Path,
    *,
    kinds: Iterable[str] = ALL_KINDS,
    delimiter: str = ";",
    decimal: str = ",",
) -> int:
    """Escribe un CSV con una fila por película, plataforma y tipo de oferta.

    Por defecto usa ``;``, coma decimal y BOM UTF-8, para que Excel en español lo abra bien.

    Args:
        db: Base de datos multiplataforma.
        dest: Ruta del CSV.
        kinds: Tipos de oferta a incluir.
        delimiter: Separador de columnas.
        decimal: Separador decimal.

    Returns:
        Filas escritas (sin la cabecera).

    Raises:
        OSError: Si no se puede escribir el fichero.
    """

    def cell(value: object) -> object:
        if isinstance(value, float):
            return f"{value:.1f}".replace(".", decimal)
        return "" if value is None else value

    rows = db.export_rows(kinds)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            out = [cell(v) for v in row]
            out[5] = KIND_LABELS.get(str(row[5]), row[5])
            writer.writerow(out)
    return len(rows)


async def _run_queue(
    items: list[int],
    handle: Callable[[int], Awaitable[None]],
    progress: HarvestProgress,
    *,
    workers: int,
    on_progress: ProgressCallback | None,
    max_consecutive_errors: int,
) -> HarvestProgress:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    consecutive = 0
    fatal: list[TmdbError] = []

    async def worker() -> None:
        nonlocal consecutive
        while not fatal:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await handle(item)
                consecutive = 0
            except TmdbError as exc:
                progress.errors += 1
                consecutive += 1
                log.warning("TMDB falló con la película %d: %s", item, exc)
                if isinstance(exc, TmdbAuthError) or consecutive >= max_consecutive_errors:
                    fatal.append(exc)
            progress.done += 1
            if on_progress:
                on_progress(progress)

    await asyncio.gather(*(worker() for _ in range(max(1, workers))))
    if fatal:
        raise TmdbError(f"Comprobación detenida: {fatal[0]}")
    return progress


def wikidata_query(prop: str) -> str:
    """Consulta SPARQL con el ID de una plataforma y, si los hay, los de TMDB e IMDb.

    Args:
        prop: Propiedad de Wikidata (``P1874``...).

    Returns:
        La consulta.
    """
    return (
        "SELECT ?ext ?tmdb ?imdb WHERE {\n"
        f"  ?item wdt:{prop} ?ext .\n"
        "  OPTIONAL { ?item wdt:P4947 ?tmdb }\n"
        "  OPTIONAL { ?item wdt:P345 ?imdb }\n"
        "}"
    )


def parse_external_csv(text: str) -> list[tuple[str, int | None, str | None]]:
    """Lee la respuesta de :func:`wikidata_query`.

    Args:
        text: CSV con columnas ``ext``, ``tmdb`` e ``imdb``.

    Returns:
        Tuplas ``(id_en_la_plataforma, tmdb_id, imdb_id)``, una por ID de plataforma.

    Raises:
        WikidataError: Si faltan las columnas esperadas.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "ext" not in reader.fieldnames:
        raise WikidataError("El CSV de Wikidata no tiene la columna «ext».")
    seen: dict[str, tuple[str, int | None, str | None]] = {}
    for row in reader:
        ext = (row.get("ext") or "").strip()
        if not ext or ext in seen:
            continue
        tmdb = (row.get("tmdb") or "").strip()
        imdb = (row.get("imdb") or "").strip()
        seen[ext] = (
            ext,
            int(tmdb) if tmdb.isdigit() else None,
            imdb if imdb.startswith("tt") else None,
        )
    return list(seen.values())


def harvest_wikidata(
    db: PlatformsDB,
    cache_dir: Path,
    platforms: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[dict[str, int], dict[str, str]]:
    """Descarga de Wikidata los IDs propios de cada plataforma.

    Un fallo en una plataforma no detiene las demás.

    Args:
        db: Base de datos multiplataforma.
        cache_dir: Carpeta donde se guardan los CSV descargados.
        platforms: Plataforma → propiedad; por defecto :data:`WIKIDATA_PLATFORMS`.
        on_progress: Se llama tras cada plataforma.

    Returns:
        ``(guardados, errores)``: IDs guardados y mensaje de error, por plataforma.
    """
    chosen = platforms if platforms is not None else WIKIDATA_PLATFORMS
    progress = HarvestProgress(stage="wikidata", total=len(chosen))
    saved: dict[str, int] = {}
    errors: dict[str, str] = {}
    for platform, prop in chosen.items():
        dest = cache_dir / f"wikidata_{platform}.csv"
        try:
            download_sparql_csv(wikidata_query(prop), dest)
            rows = parse_external_csv(dest.read_text(encoding="utf-8"))
            saved[platform] = db.replace_external_ids(platform, rows)
        except (WikidataError, OSError) as exc:
            errors[platform] = str(exc)
            progress.errors += 1
            log.warning("Wikidata falló con %s (%s): %s", platform, prop, exc)
        progress.done += 1
        if on_progress:
            on_progress(progress)
    return saved, errors


async def harvest(
    db: PlatformsDB,
    client: TmdbClient,
    *,
    cache_dir: Path,
    wikidata: bool = True,
    check: bool = True,
    check_limit: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> HarvestSummary:
    """Descarga el catálogo multiplataforma completo de la región del cliente.

    Args:
        db: Base de datos multiplataforma.
        client: Cliente de TMDB.
        cache_dir: Carpeta para los CSV de Wikidata.
        wikidata: Si se descargan los IDs propios de cada plataforma.
        check: Si se comprueba la disponibilidad exacta de cada película.
        check_limit: Máximo de películas a comprobar.
        on_progress: Progreso de cada fase.

    Returns:
        Resumen de lo descargado.

    Raises:
        TmdbError: Si TMDB rechaza la clave o falla de forma continuada.
    """
    summary = HarvestSummary()
    summary.providers = db.save_providers(await client.watch_providers(), client.region)
    providers = db.provider_ids()
    progress = HarvestProgress(stage="plataformas", total=len(providers))
    for provider_id in providers:
        try:
            summary.discovered += await discover_provider(db, client, provider_id)
        except TmdbAuthError:
            raise
        except TmdbError as exc:
            progress.errors += 1
            log.warning("No se pudo enumerar la plataforma %d: %s", provider_id, exc)
        progress.done += 1
        if on_progress:
            on_progress(progress)
    if check:
        checked = await check_availability(db, client, limit=check_limit, on_progress=on_progress)
        summary.checked, summary.check_errors = checked.done - checked.errors, checked.errors
    if wikidata:
        summary.external_ids, summary.wikidata_errors = await asyncio.to_thread(
            harvest_wikidata, db, cache_dir, None, on_progress
        )
    return summary


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
