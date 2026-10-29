#!/bin/bash
# Screen Stepper - double-click to run. First run sets things up.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Setting up for the first time..."
    python3 -m venv .venv || { echo "Python 3 not found - install it from python.org"; exit 1; }
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv/bin/python screen_stepper.py
