@echo off
chcp 65001 >nul
title Actualizador de costos Odoo
cd /d "%~dp0"

rem --- buscar Python ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo.
  echo  Falta instalar Python.
  echo.
  echo  1. Entra a https://www.python.org/downloads/ y baja la ultima version.
  echo  2. Al instalar, TILDA la casilla "Add python.exe to PATH".
  echo  3. Despues volve a hacer doble clic en "Abrir actualizador".
  echo.
  pause
  exit /b 1
)

rem --- la primera vez se prepara el entorno (tarda un par de minutos) ---
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Preparando la app por primera vez. Tarda un par de minutos...
  echo.
  %PY% -m venv .venv || goto error
)

rem --- si la app se actualizo y necesita algo nuevo, se instala ---
fc /b requirements.txt ".venv\requisitos_instalados.txt" >nul 2>nul
if errorlevel 1 (
  echo  Instalando lo necesario...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto error
  copy /y requirements.txt ".venv\requisitos_instalados.txt" >nul
)

set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m web.app
if errorlevel 1 goto error
exit /b 0

:error
echo.
echo  Algo fallo. Si la ventana muestra un error, sacale una captura y mandasela a quien te paso la app.
echo  Si es la primera vez, proba borrar la carpeta ".venv" y volver a abrir.
echo.
pause
exit /b 1
