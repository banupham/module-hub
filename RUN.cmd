@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
if not "%~1"=="" set "HUB_PORT=%~1"
if not defined HUB_PORT set "HUB_PORT=8899"
%PY% -c "import flask,requests" >nul 2>&1
if errorlevel 1 %PY% -m pip install -r requirements.txt
echo [HUB] http://127.0.0.1:%HUB_PORT%
echo [MODULE PORTS] Hub tu cap dong khi START pipeline.
start "" http://127.0.0.1:%HUB_PORT%
%PY% app.py --port %HUB_PORT%
