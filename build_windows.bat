@echo off
rem Builds a single "Screen Stepper.exe" you can copy anywhere (no Python needed to run it).
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name "Screen Stepper" ^
    --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
    screen_stepper.py
echo.
echo Done: dist\Screen Stepper.exe
pause
