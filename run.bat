@echo off
cd /d "%~dp0"
where uv >nul 2>nul || (
    echo uv is not installed. See README.md, "Setup".
    pause
    exit /b 1
)
uv run modgen.py
echo.
pause
