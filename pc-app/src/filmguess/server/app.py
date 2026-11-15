"""Aplicación FastAPI: rutas, autenticación, filtro de red y WebSocket.

Aquí no hay lógica de negocio: las rutas validan la petición, llaman a
:class:`~filmguess.game.session.GameSession` y convierten la respuesta a JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from filmguess.game.session import GameError, GameSession
from filmguess.pairing.network import (
    is_allowed_client,
    is_localhost,
    pairing_url,
    qr_svg,
    token_matches,
)
from filmguess.player.base import PlayerError
from filmguess.server import schemas
from filmguess.server.events import EventHub

log = logging.getLogger(__name__)

ERROR_STATUS: dict[str, int] = {
    "token_invalido": 401,
    "red_no_permitida": 403,
    "solo_local": 403,
    "no_encontrada": 404,
    "jugador_no_encontrado": 404,
    "ruta_no_encontrada": 404,
    "sin_ronda": 409,
    "nombre_repetido": 409,
    "demasiados_jugadores": 409,
    "sin_candidatas": 409,
    "peticion_invalida": 422,
    "nombre_invalido": 422,
    "fuera_de_duracion": 422,
    "no_disponible_es": 409,
    "perfil_requerido": 409,
    "sesion_caducada": 409,
    "navegador_cerrado": 503,
    "tiempo_agotado": 504,
    "netflix_error": 502,
}
"""Estado HTTP de cada código de error; los no listados devuelven 500."""


TV_PLACEHOLDER = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Adivina la Peli</title><style>html,body{margin:0;height:100%;background:#0b0b0d;
color:#f4f1ea;font:600 6vw system-ui,"Segoe UI",sans-serif;display:grid;place-items:center;
cursor:none}</style></head><body>Adivina la Peli</body></html>"""


async def _show_idle_when_ready(session: GameSession, attempts: int = 20) -> None:
    for _ in range(attempts):
        await asyncio.sleep(0.5)
        try:
            await session.player.idle()
        except PlayerError as exc:
            log.debug("Pantalla de espera aún no disponible: %s", exc.message)
            continue
        return
    log.warning("No se pudo abrir la pantalla de espera en el navegador de la tele.")


class ApiError(Exception):
    """Error que se devuelve como ``{ok: false, error, mensaje}``."""

    def __init__(self, code: str, message: str) -> None:
        """Crea el error.

        Args:
            code: Código de error.
            message: Mensaje en español.
        """
        super().__init__(message)
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict[str, Any]:
    """Cuerpo JSON de un error.

    Args:
        code: Código de error.
        message: Mensaje en español.

    Returns:
        ``{ok: false, error, mensaje}``.
    """
    return {"ok": False, "error": code, "mensaje": message}


class LocalNetworkOnly:
    """Middleware ASGI que rechaza clientes que no sean de la red local o del propio PC."""

    def __init__(self, app: ASGIApp) -> None:
        """Envuelve la aplicación.

        Args:
            app: Aplicación ASGI.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Filtra la petición por la IP del cliente.

        Args:
            scope: Ámbito ASGI.
            receive: Canal de entrada.
            send: Canal de salida.
        """
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = client[0] if client else None
            if not is_allowed_client(host):
                log.warning("Rechazado cliente fuera de la red local: %s", host)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps(
                    error_body("red_no_permitida", "Solo se aceptan dispositivos de tu red local.")
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def create_app(session: GameSession, token: str, *, port: int, ip: str) -> FastAPI:
    """Crea la aplicación del servidor.

    Args:
        session: Partida (catálogo, reproductor y marcador).
        token: Token de emparejamiento que deben enviar el móvil y el WebSocket.
        port: Puerto en el que escucha (para la URL del QR).
        ip: IP del PC en la red local (para la URL del QR).

    Returns:
        La aplicación FastAPI.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await session.player.start()
        except PlayerError as exc:
            # El servidor sigue: el móvil verá el error y se reintenta en la siguiente orden.
            session.last_error = {"error": exc.code, "mensaje": exc.message}
            log.error("No se pudo preparar el reproductor: %s", exc.message)
        # La pantalla de espera es una página de este servidor: se abre cuando ya escucha.
        idle_task = asyncio.create_task(_show_idle_when_ready(session))
        try:
            yield
        finally:
            idle_task.cancel()
            await session.player.close()

    app = FastAPI(
        title="Adivina la Peli", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    hub = EventHub()
    app.state.hub = hub
    app.state.session = session

    async def publish(kind: str, data: dict[str, Any]) -> None:
        if kind == "estado":
            data = schemas.status(await session.state())
        elif kind == "marcador":
            data = schemas.scoreboard(session.scoreboard.players)
        await hub.broadcast(kind, data)

    session.on_event = publish

    # --- Errores -----------------------------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), ERROR_STATUS.get(exc.code, 500))

    @app.exception_handler(GameError)
    async def game_error(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), ERROR_STATUS.get(exc.code, 500))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
        message = "Faltan datos o no son válidos" + (f": {fields}." if fields else ".")
        return JSONResponse(error_body("peticion_invalida", message), 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(error_body("ruta_no_encontrada", "Esa dirección no existe."), 404)
        return JSONResponse(error_body("error_http", str(exc.detail)), exc.status_code)

    # --- Dependencias ----------------------------------------------------------------

    async def require_token(x_token: Annotated[str | None, Header()] = None) -> None:
        if not token_matches(x_token, token):
            raise ApiError(
                "token_invalido", "El móvil no está emparejado. Escanea de nuevo el QR del PC."
            )

    async def require_localhost(request: Request) -> None:
        if not is_localhost(request.client.host if request.client else None):
            raise ApiError("solo_local", "Esta página solo se puede abrir en el propio PC.")

    authed = [Depends(require_token)]

    # --- Rutas -----------------------------------------------------------------------

    @app.get("/status", dependencies=authed)
    async def get_status() -> dict[str, Any]:
        return schemas.status(await session.state())

    @app.get("/search", dependencies=authed)
    async def get_search(
        q: Annotated[str, Query(max_length=100)] = "",
        page: Annotated[int, Query(ge=1, le=1000)] = 1,
        todas: bool = False,
    ) -> dict[str, Any]:
        return schemas.search_page(session.search(q, page=page, include_all=todas))

    @app.get("/random", dependencies=authed)
    async def get_random() -> dict[str, Any]:
        return schemas.movie_detail(session.random_movie())

    @app.get("/movie/{netflix_id}", dependencies=authed)
    async def get_movie(netflix_id: int) -> dict[str, Any]:
        return schemas.movie_detail(session.movie(netflix_id))

    @app.post("/play", dependencies=authed)
    async def post_play(body: schemas.PlayRequest) -> dict[str, Any]:
        actual = await session.play(body.netflixId, body.seconds)
        return {"ok": True, "actualSeconds": actual}

    @app.post("/pause", dependencies=authed)
    async def post_pause() -> dict[str, Any]:
        await session.pause()
        return {"ok": True}

    @app.post("/resume", dependencies=authed)
    async def post_resume() -> dict[str, Any]:
        await session.resume()
        return {"ok": True}

    @app.post("/reveal", dependencies=authed)
    async def post_reveal() -> dict[str, Any]:
        movie = await session.reveal()
        return {"ok": True, "titulo": movie.display_title} | schemas.movie_summary(movie)

    @app.post("/idle", dependencies=authed)
    async def post_idle() -> dict[str, Any]:
        await session.idle()
        return {"ok": True}

    @app.get("/players", dependencies=authed)
    async def get_players() -> dict[str, Any]:
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players", dependencies=authed, status_code=201)
    async def post_player(body: schemas.NewPlayerRequest) -> dict[str, Any]:
        await session.add_player(body.nombre)
        return schemas.scoreboard(session.scoreboard.players)

    @app.delete("/players/{player_id}", dependencies=authed)
    async def delete_player(player_id: str) -> dict[str, Any]:
        await session.remove_player(player_id)
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players/{player_id}/points", dependencies=authed)
    async def post_points(player_id: str, body: schemas.PointsRequest) -> dict[str, Any]:
        await session.add_points(player_id, body.puntos)
        return schemas.scoreboard(session.scoreboard.players)

    @app.post("/players/reset", dependencies=authed)
    async def post_reset() -> dict[str, Any]:
        await session.reset_points()
        return schemas.scoreboard(session.scoreboard.players)

    @app.get("/tv", dependencies=[Depends(require_localhost)])
    async def get_tv() -> HTMLResponse:
        # Provisional hasta F4: pantalla de espera mínima.
        return HTMLResponse(TV_PLACEHOLDER)

    @app.get("/pairing", dependencies=[Depends(require_localhost)])
    async def get_pairing() -> dict[str, Any]:
        return {"url": pairing_url(ip, port, token), "ip": ip, "puerto": port}

    @app.get("/pairing/qr.svg", dependencies=[Depends(require_localhost)])
    async def get_pairing_qr() -> Response:
        return Response(qr_svg(pairing_url(ip, port, token)), media_type="image/svg+xml")

    @app.websocket("/ws")
    async def websocket(
        websocket: WebSocket,
        token_param: Annotated[str | None, Query(alias="token")] = None,
    ) -> None:
        if not token_matches(token_param, token):
            await websocket.close(code=1008)
            return
        await hub.connect(websocket)
        try:
            await hub.send(websocket, "estado", schemas.status(await session.state()))
            await hub.send(websocket, "marcador", schemas.scoreboard(session.scoreboard.players))
            while True:
                await websocket.receive_text()  # el cliente no envía órdenes por aquí
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    app.add_middleware(LocalNetworkOnly)
    return app
