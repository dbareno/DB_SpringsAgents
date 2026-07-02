@echo off
title Spring Design Agent
echo ============================================
echo   Spring Design Agent - Iniciando...
echo ============================================
echo.

REM Activar entorno virtual
call "%~dp0venv\Scripts\activate.bat"

REM Iniciar servidor
python "%~dp0scripts\launcher.py"

if %errorlevel% neq 0 (
    echo.
    echo [!] Hubo un error. Presiona una tecla para salir.
    pause >nul
)
