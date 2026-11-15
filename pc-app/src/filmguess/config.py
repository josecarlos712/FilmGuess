"""Carga, validación y persistencia de la configuración de la app de PC."""

from __future__ import annotations

import secrets
import shutil
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field, ValidationError

APP_DIR = Path(__file__).resolve().parents[2]
"""Carpeta ``pc-app/``: base para resolver rutas relativas."""

DEFAULT_CONFIG_PATH = APP_DIR / "config.toml"
EXAMPLE_CONFIG_PATH = APP_DIR / "config.example.toml"


class ConfigError(Exception):
    """La configuración no existe, no se puede leer o no es válida."""


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8765, ge=1, le=65535)


class PairingConfig(BaseModel):
    token: str = ""


class BrowserConfig(BaseModel):
    channel: Literal["msedge", "chrome"] = "msedge"
    profile_dir: Path = Path("../data/browser-profile")
    disable_gpu: bool = False
    fullscreen: bool = True


class NetflixConfig(BaseModel):
    profile_name: str = ""
    play_timeout_s: float = Field(default=15, gt=0)
    selectors: dict[str, str] = {}
    """Cambios a los selectores de Netflix (ver ``player/overlays.py``)."""


class TmdbConfig(BaseModel):
    api_key: str = ""
    language: str = "es-ES"
    region: str = "ES"


class CatalogConfig(BaseModel):
    data_dir: Path = Path("../data")
    csv_file: Path = Path("../data/movies.csv")


class RandomConfig(BaseModel):
    min_year: int = 1991
    min_votes: int = Field(default=25000, ge=0)
    pool_size: int = Field(default=500, ge=1)
    no_repeat: int = Field(default=50, ge=0)


class AppConfig(BaseModel):
    """Configuración completa, tal como se guarda en ``config.toml``."""

    server: ServerConfig = ServerConfig()
    pairing: PairingConfig = PairingConfig()
    browser: BrowserConfig = BrowserConfig()
    netflix: NetflixConfig = NetflixConfig()
    tmdb: TmdbConfig = TmdbConfig()
    catalog: CatalogConfig = CatalogConfig()
    random: RandomConfig = RandomConfig()

    def resolve(self, path: Path) -> Path:
        """Resuelve una ruta de la configuración respecto a ``pc-app/``.

        Args:
            path: Ruta absoluta o relativa a ``pc-app/``.

        Returns:
            La ruta absoluta.
        """
        return path if path.is_absolute() else (APP_DIR / path).resolve()


def generate_token() -> str:
    """Genera un token de emparejamiento de 128 bits.

    Returns:
        Token URL-safe.
    """
    return secrets.token_urlsafe(16)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Lee la configuración; si falta, la crea desde el ejemplo y genera el token.

    Args:
        path: Ruta del ``config.toml``.

    Returns:
        La configuración validada, siempre con token.

    Raises:
        ConfigError: Si el fichero no se puede leer o tiene valores no válidos.
    """
    if not path.exists():
        if not EXAMPLE_CONFIG_PATH.exists():
            raise ConfigError(f"No existe {path} ni la plantilla {EXAMPLE_CONFIG_PATH}.")
        shutil.copyfile(EXAMPLE_CONFIG_PATH, path)

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"No se pudo leer {path}: {exc}") from exc

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Configuración no válida en {path}:\n{exc}") from exc

    if not config.pairing.token:
        config.pairing.token = generate_token()
        save_config(config, path)
    return config


def save_config(config: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Guarda la configuración en TOML (se pierden los comentarios de la plantilla).

    Args:
        config: Configuración a guardar.
        path: Ruta de destino.

    Raises:
        ConfigError: Si no se puede escribir el fichero.
    """
    data = config.model_dump(mode="json")
    try:
        path.write_text(tomli_w.dumps(data), encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"No se pudo guardar {path}: {exc}") from exc
