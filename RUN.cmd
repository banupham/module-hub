@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% -c "import flask,requests" >nul 2>&1
if errorlevel 1 %PY% -m pip install -r requirements.txt
start "" http://127.0.0.1:8899
%PY% app.py
