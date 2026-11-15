"""Tests de carga y persistencia de la configuración."""

from __future__ import annotations

from pathlib import Path

import pytest

from filmguess import config as cfg
from filmguess.cli import main


def test_crea_config_desde_ejemplo_y_genera_token(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    loaded = cfg.load_config(path)

    assert path.exists()
    assert len(loaded.pairing.token) >= 20
    assert loaded.browser.channel == "msedge"
    # El token generado se persiste y se reutiliza.
    assert cfg.load_config(path).pairing.token == loaded.pairing.token


def test_respeta_valores_existentes(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[pairing]\ntoken = "abc"\n[browser]\nchannel = "chrome"\n', encoding="utf-8")

    loaded = cfg.load_config(path)

    assert loaded.pairing.token == "abc"
    assert loaded.browser.channel == "chrome"
    assert loaded.server.port == 8765


@pytest.mark.parametrize(
    "contenido",
    ['[browser]\nchannel = "firefox"\n', "[server]\nport = 0\n", "esto no es toml = ["],
)
def test_config_no_valida_lanza_error(tmp_path: Path, contenido: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(contenido, encoding="utf-8")

    with pytest.raises(cfg.ConfigError):
        cfg.load_config(path)


def test_resolve_rutas_relativas_a_pc_app() -> None:
    resolved = cfg.AppConfig().resolve(Path("../data"))

    assert resolved == (cfg.APP_DIR.parent / "data").resolve()


def test_cli_config_oculta_clave(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[tmdb]\napi_key = "secreta"\n', encoding="utf-8")

    assert main(["--config", str(path), "config"]) == 0

    out = capsys.readouterr().out
    assert "secreta" not in out
    assert "****" in out


def test_cli_config_erronea_devuelve_2(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = -1\n", encoding="utf-8")

    assert main(["--config", str(path), "config"]) == 2
