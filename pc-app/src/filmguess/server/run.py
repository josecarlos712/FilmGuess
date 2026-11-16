"""Arranque del servidor: monta la partida, muestra el QR y lanza Uvicorn."""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.importer import ImportProgress
from filmguess.catalog.seed import install_seed
from filmguess.catalog.service import ImportOptions, ImportSummary, db_path, run_import
from filmguess.catalog.tmdb import TmdbClient
from filmguess.config import AppConfig, save_config
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.pairing.network import local_ip, pairing_url, qr_text
from filmguess.panel.service import PanelService
from filmguess.player.base import Player
from filmguess.player.browser import Browser
from filmguess.player.netflix import NetflixPlayer
from filmguess.player.overlays import init_script
from filmguess.server.app import create_app

SCOREBOARD_FILENAME = "scoreboard.json"
LOG_FILENAME = "filmguess.log"
LOG_MAX_BYTES = 1_000_000
_TOKEN_IN_URL = re.compile(r"(token=)[^&\s\"]+")


class RedactToken(logging.Filter):
    """Oculta el token en los registros (el WebSocket lo recibe en ``?token=``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Sustituye el valor de ``token=`` por asteriscos.

        Args:
            record: Registro a escribir.

        Returns:
            Siempre ``True``: el registro se escribe, solo que sin el token.
        """
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1****", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1****", record.msg)
        return True


def log_to_file(path: Path) -> None:
    """Guarda también el registro en un fichero (rotado), para revisar fallos después.

    Args:
        path: Ruta del fichero de registro.
    """
    handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def serve(config: AppConfig, player: Player | None = None) -> None:
    """Arranca el servidor y el navegador, y bloquea hasta que se detiene (Ctrl+C).

    Args:
        config: Configuración de la app (con token).
        player: Reproductor de la tele; por defecto, Netflix en el navegador configurado.

    Raises:
        ScoreboardError: Si el marcador guardado está dañado.
        SeedError: Si el catálogo incluido está dañado.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    data_dir = config.resolve(config.catalog.data_dir)
    log_to_file(data_dir / LOG_FILENAME)
    if install_seed(db_path(config)):
        print("Catálogo incluido instalado: ya se puede jugar sin importar nada.")
    db = CatalogDB(db_path(config))
    try:
        session = GameSession(
            db,
            player or netflix_player(config),
            Scoreboard(data_dir / SCOREBOARD_FILENAME),
            config.random,
        )
        ip = local_ip()
        panel = panel_service(config, db)
        app = create_app(session, config.pairing.token, port=config.server.port, ip=ip, panel=panel)
        url = pairing_url(ip, config.server.port, config.pairing.token)
        print("Adivina la Peli: escanea este QR con el móvil (misma WiFi que el PC).")
        print(qr_text(url))
        print(f"O abre en el móvil: {url}")
        print(f"Panel del PC: http://localhost:{config.server.port}/panel  ·  Ctrl+C para salir.")
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level="info",
            log_config=uvicorn_log_config(),
        )
    finally:
        db.close()


def uvicorn_log_config() -> dict[str, Any]:
    """Configuración de registros de Uvicorn con el token oculto en todos sus mensajes.

    Returns:
        Copia de la configuración por defecto de Uvicorn con :class:`RedactToken`.
    """
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config.setdefault("filters", {})["redact_token"] = {"()": RedactToken}
    for handler in log_config["handlers"].values():
        handler.setdefault("filters", []).append("redact_token")
    return log_config


def panel_service(config: AppConfig, db: CatalogDB) -> PanelService:
    """Servicio del panel con el guardado del token, TMDB y la importación reales.

    Args:
        config: Configuración (se reescribe ``config.toml`` al cambiar el token).
        db: Catálogo abierto por el servidor.

    Returns:
        El servicio.
    """

    def save_token(token: str) -> None:
        config.pairing.token = token
        save_config(config)

    def tmdb() -> TmdbClient:
        return TmdbClient(
            config.tmdb.api_key, language=config.tmdb.language, region=config.tmdb.region
        )

    def run(progress: Callable[[ImportProgress], None]) -> ImportSummary:
        return run_import(config, ImportOptions(), progress)

    return PanelService(
        db,
        token=config.pairing.token,
        save_token=save_token,
        tmdb=tmdb if config.tmdb.api_key else None,
        run_import=run,
    )


def netflix_player(config: AppConfig) -> NetflixPlayer:
    """Reproductor de Netflix con el navegador configurado.

    Args:
        config: Configuración de la app.

    Returns:
        El reproductor (el navegador se abre al arrancar el servidor).
    """
    browser = Browser(
        config.browser,
        config.resolve(config.browser.profile_dir),
        init_script(config.netflix.selectors),
    )
    return NetflixPlayer(
        browser,
        idle_url=f"http://127.0.0.1:{config.server.port}/tv",
        profile_name=config.netflix.profile_name,
        timeout_s=config.netflix.play_timeout_s,
    )
