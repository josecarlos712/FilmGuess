"""Punto de entrada de la app de PC: ``python src/main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# Permite ejecutar ``python src/main.py`` sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from filmguess.cli import main

if __name__ == "__main__":
    # Acentos correctos aunque la salida se redirija (Git Bash, ficheros de log).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
