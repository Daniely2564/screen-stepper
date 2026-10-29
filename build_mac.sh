#!/bin/bash
# Builds "Screen Stepper.app" in ./dist
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller
.venv/bin/pyinstaller --noconfirm --windowed --name "Screen Stepper" \
    --hidden-import pynput.keyboard._darwin --hidden-import pynput.mouse._darwin \
    screen_stepper.py
echo "Done: dist/Screen Stepper.app"
