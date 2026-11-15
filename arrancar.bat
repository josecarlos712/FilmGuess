@echo off
rem Arranca Adivina la Peli. Uso: arrancar.bat [comando]
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No existe el entorno .venv. Sigue los pasos de instalacion del README.
  exit /b 1
)
".venv\Scripts\python.exe" "pc-app\src\main.py" %*
