"""Arranque del servidor: monta la partida, muestra el QR y lanza Uvicorn."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from filmguess.catalog.db import CatalogDB
from filmguess.catalog.service import db_path
from filmguess.config import AppConfig
from filmguess.game.scoreboard import Scoreboard
from filmguess.game.session import GameSession
from filmguess.pairing.network import local_ip, pairing_url, qr_text
from filmguess.player.base import Player, UnavailablePlayer
from filmguess.server.app import create_app

SCOREBOARD_FILENAME = "scoreboard.json"
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


def serve(config: AppConfig, player: Player | None = None) -> None:
    """Arranca el servidor y bloquea hasta que se detiene (Ctrl+C).

    Args:
        config: Configuración de la app (con token).
        player: Reproductor de la tele; sin él, las órdenes responden ``navegador_cerrado``.

    Raises:
        ScoreboardError: Si el marcador guardado está dañado.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    data_dir = config.resolve(config.catalog.data_dir)
    db = CatalogDB(db_path(config))
    try:
        session = GameSession(
            db,
            player or UnavailablePlayer(),
            Scoreboard(data_dir / SCOREBOARD_FILENAME),
            config.random,
        )
        ip = local_ip()
        app = create_app(session, config.pairing.token, port=config.server.port, ip=ip)
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
