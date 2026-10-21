@echo off
setlocal
cd /d "%~dp0"
set "HYDRO_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "HYDRO_PYTHON=%~dp0.venv\Scripts\python.exe"
"%HYDRO_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer and enable Add Python to PATH.
    pause
    exit /b 1
)
"%HYDRO_PYTHON%" hydrographic_package.py
if errorlevel 1 (
    echo.
    echo HYDROGEOMATICS could not start. Read the error above.
    echo Extract the complete package before running it. See README.md for dependency setup.
    pause
    exit /b 1
)
