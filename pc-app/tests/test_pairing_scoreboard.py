"""Tests de la red local, el token, el QR y el marcador."""

from __future__ import annotations

from pathlib import Path

import pytest

from filmguess.game.scoreboard import MAX_PLAYERS, Scoreboard, ScoreboardError
from filmguess.pairing.network import (
    is_allowed_client,
    is_localhost,
    local_ip,
    pairing_url,
    qr_svg,
    qr_text,
    token_matches,
)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("192.168.1.20", True),
        ("10.0.0.5", True),
        ("172.16.3.4", True),
        ("169.254.1.1", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("fe80::1%12", True),
        ("::ffff:192.168.1.7", True),
        ("8.8.8.8", False),
        ("::ffff:8.8.8.8", False),
        ("testclient", False),
        ("", False),
        (None, False),
    ],
)
def test_is_allowed_client(host: str | None, allowed: bool) -> None:
    assert is_allowed_client(host) is allowed


def test_is_localhost() -> None:
    assert is_localhost("127.0.0.1")
    assert is_localhost("::1")
    assert not is_localhost("192.168.1.20")
    assert not is_localhost(None)


def test_token_matches() -> None:
    assert token_matches("abc", "abc")
    assert not token_matches("abd", "abc")
    assert not token_matches(None, "abc")
    assert not token_matches("", "")


def test_pairing_url_y_qr() -> None:
    url = pairing_url("192.168.1.10", 8765, "tok")

    assert url == "http://192.168.1.10:8765/m/#token=tok"
    assert qr_svg(url).lstrip().startswith(b"<?xml")
    assert len(qr_text(url).splitlines()) > 10


def test_local_ip_devuelve_una_ip() -> None:
    assert is_allowed_client(local_ip()) or local_ip().count(".") == 3


def test_scoreboard_validaciones() -> None:
    board = Scoreboard(None)

    with pytest.raises(ScoreboardError, match="Escribe un nombre"):
        board.add("   ")
    with pytest.raises(ScoreboardError, match="caracteres"):
        board.add("x" * 31)
    for i in range(MAX_PLAYERS):
        board.add(f"Jugador {i}")
    with pytest.raises(ScoreboardError) as exc:
        board.add("Uno más")
    assert exc.value.code == "demasiados_jugadores"


def test_scoreboard_puntos_negativos_y_persistencia(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "scoreboard.json"
    board = Scoreboard(path)
    ana = board.add("Ana")

    board.add_points(ana.id, 5)
    board.add_points(ana.id, -2)

    assert Scoreboard(path).players[0].points == 3


def test_scoreboard_danado(tmp_path: Path) -> None:
    path = tmp_path / "scoreboard.json"
    path.write_text("{no es json", encoding="utf-8")

    with pytest.raises(ScoreboardError) as exc:
        Scoreboard(path)
    assert exc.value.code == "marcador_danado"


def test_los_registros_no_muestran_el_token() -> None:
    import logging

    from filmguess.server.run import RedactToken, uvicorn_log_config

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s"',
        ("1.2.3.4", "GET", "/ws?token=abc&x=1"),
        None,
    )
    assert RedactToken().filter(record)
    assert record.getMessage() == '1.2.3.4 - "GET /ws?token=****&x=1"'
    config = uvicorn_log_config()
    assert all("redact_token" in h["filters"] for h in config["handlers"].values())
