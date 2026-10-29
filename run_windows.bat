@echo off
rem Screen Stepper - first run sets things up, later runs just start the app.
cd /d "%~dp0"
if not exist .venv (
    echo Setting up for the first time...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
start "" .venv\Scripts\pythonw.exe screen_stepper.py
