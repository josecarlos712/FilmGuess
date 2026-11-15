"""Difusión de eventos en tiempo real a los WebSocket conectados."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class EventHub:
    """Clientes WebSocket conectados y envío de eventos a todos ellos."""

    def __init__(self) -> None:
        """Crea el difusor sin clientes."""
        self._clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        """Número de clientes conectados."""
        return len(self._clients)

    def count_role(self, role: str) -> int:
        """Clientes conectados con un papel (``movil`` o ``tele``).

        Args:
            role: Papel.

        Returns:
            Cuántos hay.
        """
        return sum(1 for r in self._clients.values() if r == role)

    async def connect(self, websocket: WebSocket, role: str = "movil") -> None:
        """Acepta y registra un cliente.

        Args:
            websocket: Conexión entrante.
            role: Papel del cliente (``movil`` o ``tele``).
        """
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = role

    async def close_role(self, role: str, code: int = 1008) -> int:
        """Cierra las conexiones de un papel (p. ej. los móviles al cambiar el token).

        Args:
            role: Papel.
            code: Código de cierre.

        Returns:
            Cuántas se han cerrado.
        """
        async with self._lock:
            targets = [ws for ws, r in self._clients.items() if r == role]
            for websocket in targets:
                del self._clients[websocket]
        for websocket in targets:
            with contextlib.suppress(RuntimeError, OSError):
                await websocket.close(code=code)
        return len(targets)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Olvida un cliente.

        Args:
            websocket: Conexión cerrada.
        """
        async with self._lock:
            self._clients.pop(websocket, None)

    async def send(self, websocket: WebSocket, kind: str, data: dict[str, Any]) -> None:
        """Envía un evento a un solo cliente.

        Args:
            websocket: Cliente.
            kind: Tipo de evento (``estado``, ``error``, ``marcador``).
            data: Datos del evento.
        """
        await websocket.send_json({"tipo": kind, "datos": data})

    async def broadcast(self, kind: str, data: dict[str, Any]) -> None:
        """Envía un evento a todos; los clientes que fallan se descartan.

        Args:
            kind: Tipo de evento.
            data: Datos del evento.
        """
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for websocket in clients:
            try:
                await self.send(websocket, kind, data)
            except (RuntimeError, OSError) as exc:
                log.info("WebSocket descartado: %s", exc)
                dead.append(websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._clients.pop(websocket, None)
                    with contextlib.suppress(RuntimeError, OSError):
                        await websocket.close()
