"""Construye el paquete de Adivina la Peli para Windows: un .zip listo para jugar.

El paquete lleva un Python portátil (la distribución «embeddable» oficial de
python.org) con las dependencias ya instaladas, así que en el PC de destino no hace
falta instalar Python ni nada más que Edge o Chrome.

Estructura (la misma que el repositorio, para que las rutas relativas sigan valiendo)::

    AdivinaLaPeli/
      Jugar.bat                     arranca el juego
      Iniciar sesion en Netflix.bat abre Netflix para iniciar sesión una vez
      LEEME.txt
      python/                       Python portátil + dependencias
      pc-app/src, pc-app/web, pc-app/catalogo, pc-app/config.example.toml
      mobile-app/
      data/                         se crea al jugar (catálogo, marcador, perfil)

No se copian ``pc-app/config.toml`` (lleva el token del PC) ni ``data/``.

Uso::

    .venv\\Scripts\\python.exe scripts\\build_release.py

Deja ``dist/AdivinaLaPeli-<fecha>-<commit>.zip``.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CACHE = ROOT / "build" / "cache"
NAME = "AdivinaLaPeli"

PYTHON_VERSION = "3.12.10"
"""Versión del Python portátil; la misma serie que el entorno de desarrollo."""

EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)

COPY_DIRS = ["pc-app/src", "pc-app/web", "pc-app/catalogo", "mobile-app"]
COPY_FILES = ["pc-app/config.example.toml", "LICENSE.md", "NOTICE"]
SKIP = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.egg-info", ".mypy_cache", ".ruff_cache", "_preview*"
)

JUGAR_BAT = r"""@echo off
rem Adivina la Peli: arranca el juego. Deja esta ventana abierta mientras juegas.
cd /d "%~dp0"
"python\python.exe" "pc-app\src\main.py" %*
if errorlevel 1 pause
"""

LOGIN_BAT = r"""@echo off
rem Abre Netflix en una ventana normal para iniciar sesion (solo hace falta una vez).
cd /d "%~dp0"
"python\python.exe" "pc-app\src\main.py" netflix-login
if errorlevel 1 pause
"""

LEEME = """Adivina la Peli
===============

Requisitos del PC
  - Windows 10 u 11 (64 bits).
  - Microsoft Edge (viene con Windows) o Google Chrome.
  - Una cuenta de Netflix.
  No hace falta instalar Python: va incluido en la carpeta «python».

Primera vez
  1. Descomprime el .zip en una carpeta que no sea de solo lectura
     (por ejemplo Documentos\\AdivinaLaPeli).
  2. Haz doble clic en «Iniciar sesion en Netflix.bat», entra en tu cuenta y
     cierra esa ventana.
  3. Haz doble clic en «Jugar.bat». La primera vez crea pc-app\\config.toml con
     los ajustes (ahí puedes poner, por ejemplo, el nombre del perfil de Netflix).

Para jugar
  - Haz doble clic en «Jugar.bat» y deja abierta la ventana negra.
  - En la tele sale un código QR: escanéalo con el móvil (en la misma wifi).
  - Si Windows pregunta por el firewall, permite el acceso en redes privadas para
    que el móvil pueda conectarse.

Catálogo
  El juego trae su catálogo de películas. Las actualizaciones llegan como
  archivos aparte; sigue las instrucciones que vengan con cada una.

Licencia
  PolyForm Noncommercial 1.0.0 (LICENSE.md): gratis para usar y modificar sin
  fines comerciales; no se puede vender. Créditos: TMDB, Wikiquote y Wikidata.
  Proyecto no oficial, sin relación con Netflix.

Tus datos (marcador, catálogo instalado y sesión de Netflix) se guardan en la
carpeta «data». Si actualizas el juego, conserva «data» y «pc-app\\config.toml».
"""


def run(*args: str) -> str:
    """Ejecuta un comando y devuelve su salida.

    Args:
        *args: El comando y sus argumentos.

    Returns:
        La salida estándar, sin espacios al final.

    Raises:
        subprocess.CalledProcessError: Si el comando falla.
    """
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def dependencies() -> list[str]:
    """Dependencias de ``pc-app/pyproject.toml`` (sin las de desarrollo).

    Returns:
        Los requisitos tal como están en ``[project] dependencies``.
    """
    data = tomllib.loads(
        (ROOT / "pc-app" / "pyproject.toml").read_text(encoding="utf-8")
    )
    deps: list[str] = data["project"]["dependencies"]
    return deps


def embed_zip() -> bytes:
    """Python portátil de python.org, descargado una vez y guardado en ``build/cache``.

    Returns:
        El contenido del .zip.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / Path(EMBED_URL).name
    if not cached.exists():
        print(f"Descargando {EMBED_URL}")
        with urllib.request.urlopen(EMBED_URL, timeout=120) as resp:
            cached.write_bytes(resp.read())
    return cached.read_bytes()


def install_python(target: Path) -> None:
    """Descomprime el Python portátil y le instala las dependencias del juego.

    Args:
        target: Carpeta ``python`` del paquete.
    """
    with zipfile.ZipFile(io.BytesIO(embed_zip())) as archive:
        archive.extractall(target)
    # El Python portátil no carga site-packages salvo que su ._pth lo diga.
    major_minor = "".join(PYTHON_VERSION.split(".")[:2])
    pth = target / f"python{major_minor}._pth"
    pth.write_text(
        f"python{major_minor}.zip\n.\nLib\\site-packages\nimport site\n",
        encoding="utf-8",
    )
    site_packages = target / "Lib" / "site-packages"
    print("Instalando dependencias:", ", ".join(dependencies()))
    run(
        sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
        "--target", str(site_packages),
        "--only-binary=:all:", "--platform", "win_amd64",
        "--python-version", ".".join(PYTHON_VERSION.split(".")[:2]),
        "--implementation", "cp",
        *dependencies(),
    )  # fmt: skip


def copy_app(target: Path) -> None:
    """Copia el juego con la misma estructura que el repositorio.

    Args:
        target: Raíz del paquete.
    """
    for rel in COPY_DIRS:
        shutil.copytree(ROOT / rel, target / rel, ignore=SKIP)
    for rel in COPY_FILES:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target / rel)
    (target / "Jugar.bat").write_text(JUGAR_BAT.replace("\n", "\r\n"), encoding="ascii")
    (target / "Iniciar sesion en Netflix.bat").write_text(
        LOGIN_BAT.replace("\n", "\r\n"), encoding="ascii"
    )
    (target / "LEEME.txt").write_text(LEEME.replace("\n", "\r\n"), encoding="utf-8-sig")


def check(target: Path) -> None:
    """Comprueba que el Python del paquete importa el juego y sus dependencias.

    Args:
        target: Raíz del paquete.

    Raises:
        subprocess.CalledProcessError: Si algo no se puede importar.
    """
    code = (
        "import sys; sys.path.insert(0, r'pc-app\\src');"
        "import fastapi, uvicorn, playwright, httpx, pydantic, qrcode, tomli_w;"
        "import filmguess.server.app, filmguess.player.netflix, filmguess.cli;"
        "print('ok', sys.version.split()[0])"
    )
    out = subprocess.run(
        [str(target / "python" / "python.exe"), "-c", code],
        check=True, capture_output=True, text=True, cwd=target,
    ).stdout.strip()  # fmt: skip
    print("Comprobación:", out)


def main() -> int:
    """Construye el paquete y el .zip.

    Returns:
        Código de salida (0 si todo va bien).
    """
    commit = run("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD")
    dirty = bool(run("git", "-C", str(ROOT), "status", "--porcelain"))
    stamp = f"{date.today():%Y%m%d}-{commit}{'-local' if dirty else ''}"
    target = DIST / NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    install_python(target / "python")
    copy_app(target)
    (target / "VERSION.txt").write_text(f"{stamp}\n", encoding="utf-8")
    check(target)

    zip_path = DIST / f"{NAME}-{stamp}.zip"
    zip_path.unlink(missing_ok=True)
    print(f"Comprimiendo {zip_path.name}")
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(target.rglob("*")):
            archive.write(path, Path(NAME) / path.relative_to(target))
    size = zip_path.stat().st_size / 1_000_000
    print(f"Listo: {zip_path} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
