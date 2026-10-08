@echo off
setlocal enabledelayedexpansion
title Minecraft Auto Launcher

:: Obtener la ruta dinámica de la carpeta donde se encuentra este script
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: 1. Comprobar e instalar Python si no existe
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python no detectado en este equipo. Descargando instalador...
    set "PYTHON_URL=https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
    set "INSTALLER_PATH=%temp%\python_installer.exe"
    powershell -Command "Invoke-WebRequest -Uri '!PYTHON_URL!' -OutFile '!INSTALLER_PATH!' -UseBasicParsing"
    start /wait "" "!INSTALLER_PATH!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
    del "!INSTALLER_PATH!"
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%PATH%"
)

:: 2. Instalar librería requerida
python -m pip install minecraft-launcher-lib --quiet

:: 3. Ejecutar launcher.py desde su propia ubicación relativa
cls
python "%SCRIPT_DIR%launcher.py" %*

if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [!] Ocurrio un problema durante la ejecucion.
    echo ============================================================
    pause
)