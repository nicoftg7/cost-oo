@echo off
chcp 65001 >nul
title Actualizador de costos - EJEMPLO
cd /d "%~dp0"

rem --- el ejemplo usa su propia carpeta de datos: nunca toca la de un negocio real ---
set "DEMO=%~dp0ejemplo\datos_demo"
if exist "%DEMO%" rmdir /s /q "%DEMO%"
xcopy /e /i /q "ejemplo\memoria" "%DEMO%\memoria" >nul
rem (sin tildes en el .bat: con chcp 65001 cmd lee mal las lineas que siguen a una tilde)
> "%DEMO%\config.json" echo {"configurado": true, "negocio": "Almac\u00e9n de ejemplo"}

set "ACTUALIZADOR_DATOS=%DEMO%"
set ACTUALIZADOR_EJEMPLO=1
set ACTUALIZADOR_PUERTO=8770
call "%~dp0Abrir actualizador.bat"
