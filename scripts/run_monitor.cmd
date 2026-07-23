@echo off
setlocal
set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    "%PROJECT_DIR%\.venv\Scripts\python.exe" "%PROJECT_DIR%\main.py" --config "%PROJECT_DIR%\config.json"
) else (
    py -3 "%PROJECT_DIR%\main.py" --config "%PROJECT_DIR%\config.json"
)

exit /b %ERRORLEVEL%
