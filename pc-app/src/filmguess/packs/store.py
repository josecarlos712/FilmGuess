"""Packs de películas guardados como ficheros JSON en ``data/packs/``.

Un pack separa las películas que el grupo conoce de las que no, y fija qué parte de las
rondas al azar sale de cada lista. Cada pack es un fichero ``<slug>.json`` que se puede
copiar a otro PC o editar a mano.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKS_DIRNAME = "packs"
DEFAULT_KNOWN_RATIO = 0.7
MAX_NAME_LENGTH = 60
MAX_MOVIES = 5000

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PackError(Exception):
    """Pack inexistente, repetido o con datos no válidos."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código estable (``pack_no_encontrado``, ``pack_invalido``…).
            message: Mensaje para mostrar.
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Pack:
    """Un pack de películas."""

    slug: str
    name: str
    known: tuple[int, ...] = ()
    """IDs de Netflix de las películas que el grupo conoce."""
    unknown: tuple[int, ...] = ()
    """IDs de Netflix de las que no conoce (para que no sea tan fácil)."""
    known_ratio: float = DEFAULT_KNOWN_RATIO
    """Parte de las rondas al azar que sale de las conocidas (de 0 a 1)."""

    @property
    def size(self) -> int:
        """Número total de películas."""
        return len(self.known) + len(self.unknown)


def slugify(name: str) -> str:
    """Convierte un nombre en un identificador apto para nombre de fichero.

    Args:
        name: Nombre del pack.

    Returns:
        Minúsculas, sin acentos y con guiones; vacío si no queda ninguna letra o número.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_name.lower()))[:MAX_NAME_LENGTH].strip("-")


def build_pack(
    slug: str,
    name: str,
    known: list[int],
    unknown: list[int],
    known_ratio: float = DEFAULT_KNOWN_RATIO,
) -> Pack:
    """Valida los datos de un pack y lo crea.

    Quita los IDs repetidos; si una película está en las dos listas, cuenta como conocida.

    Args:
        slug: Identificador (nombre del fichero sin extensión).
        name: Nombre visible.
        known: IDs de las películas conocidas.
        unknown: IDs de las no conocidas.
        known_ratio: Parte de las rondas que sale de las conocidas.

    Returns:
        El pack.

    Raises:
        PackError: Si el identificador, el nombre, los IDs o la proporción no valen.
    """
    if not _SLUG.fullmatch(slug) or len(slug) > MAX_NAME_LENGTH:
        raise PackError("pack_invalido", "El identificador del pack no es válido.")
    clean_name = " ".join(name.split())
    if not clean_name or len(clean_name) > MAX_NAME_LENGTH:
        raise PackError(
            "pack_invalido", f"El nombre debe tener entre 1 y {MAX_NAME_LENGTH} caracteres."
        )
    if not 0 <= known_ratio <= 1:
        raise PackError("pack_invalido", "La proporción de conocidas debe estar entre 0 y 1.")
    known_ids = _clean_ids(known)
    known_set = set(known_ids)
    unknown_ids = tuple(i for i in _clean_ids(unknown) if i not in known_set)
    if len(known_ids) + len(unknown_ids) > MAX_MOVIES:
        raise PackError("pack_invalido", f"Un pack admite como máximo {MAX_MOVIES} películas.")
    return Pack(slug, clean_name, known_ids, unknown_ids, float(known_ratio))


def _clean_ids(ids: list[int]) -> tuple[int, ...]:
    seen: dict[int, None] = {}
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PackError("pack_invalido", "Los IDs de las películas deben ser números.")
        seen.setdefault(value)
    return tuple(seen)


class PackStore:
    """Lee y escribe los packs de una carpeta."""

    def __init__(self, directory: Path) -> None:
        """Crea el almacén (la carpeta se crea al guardar el primer pack).

        Args:
            directory: Carpeta de los packs.
        """
        self.directory = directory

    def list(self) -> list[Pack]:
        """Packs guardados, por nombre. Los ficheros dañados se omiten.

        Returns:
            Los packs válidos.
        """
        if not self.directory.is_dir():
            return []
        packs: list[Pack] = []
        for path in self.directory.glob("*.json"):
            try:
                packs.append(self._read(path))
            except PackError:
                continue
        return sorted(packs, key=lambda p: p.name.casefold())

    def get(self, slug: str) -> Pack:
        """Lee un pack.

        Args:
            slug: Identificador.

        Returns:
            El pack.

        Raises:
            PackError: Si no existe o el fichero está dañado.
        """
        path = self._path(slug)
        if not path.is_file():
            raise PackError("pack_no_encontrado", "Ese pack no existe.")
        return self._read(path)

    def create(self, name: str) -> Pack:
        """Crea un pack vacío.

        Args:
            name: Nombre visible.

        Returns:
            El pack creado.

        Raises:
            PackError: Si el nombre no vale o ya hay un pack con ese nombre.
        """
        slug = slugify(name)
        if not slug:
            raise PackError("pack_invalido", "El nombre debe tener alguna letra o número.")
        pack = build_pack(slug, name, [], [])
        if self._path(slug).exists():
            raise PackError("pack_repetido", "Ya hay un pack con ese nombre.")
        self.save(pack)
        return pack

    def save(self, pack: Pack) -> None:
        """Guarda (o sustituye) un pack.

        Args:
            pack: Pack ya validado con :func:`build_pack`.

        Raises:
            PackError: Si no se puede escribir el fichero.
        """
        data = {
            "nombre": pack.name,
            "proporcionConocidas": pack.known_ratio,
            "conocidas": list(pack.known),
            "desconocidas": list(pack.unknown),
        }
        path = self._path(pack.slug)
        tmp = path.with_suffix(".tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise PackError("pack_no_guardado", f"No se pudo guardar el pack: {exc}") from exc

    def delete(self, slug: str) -> None:
        """Borra un pack.

        Args:
            slug: Identificador.

        Raises:
            PackError: Si no existe o no se puede borrar.
        """
        path = self._path(slug)
        if not path.is_file():
            raise PackError("pack_no_encontrado", "Ese pack no existe.")
        try:
            path.unlink()
        except OSError as exc:
            raise PackError("pack_no_guardado", f"No se pudo borrar el pack: {exc}") from exc

    def _path(self, slug: str) -> Path:
        if not _SLUG.fullmatch(slug) or len(slug) > MAX_NAME_LENGTH:
            raise PackError("pack_no_encontrado", "Ese pack no existe.")
        return self.directory / f"{slug}.json"

    def _read(self, path: Path) -> Pack:
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PackError("pack_invalido", f"No se puede leer {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise PackError("pack_invalido", f"{path.name} no tiene el formato de un pack.")
        known = data.get("conocidas", [])
        unknown = data.get("desconocidas", [])
        ratio = data.get("proporcionConocidas", DEFAULT_KNOWN_RATIO)
        name = data.get("nombre", path.stem)
        if (
            not isinstance(known, list)
            or not isinstance(unknown, list)
            or not isinstance(name, str)
            or isinstance(ratio, bool)
            or not isinstance(ratio, int | float)
        ):
            raise PackError("pack_invalido", f"{path.name} no tiene el formato de un pack.")
        return build_pack(path.stem, name, known, unknown, float(ratio))
