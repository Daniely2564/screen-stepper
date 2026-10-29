# Screen Stepper

Takes a screenshot of an app, presses a key (→ Right arrow by default), takes another screenshot, and keeps going until you stop it. Handy for paging through slides, PDFs, e-books or image viewers.

## Run it

**Windows** – install Python 3 from python.org (tick "Add python.exe to PATH"), then double-click `run_windows.bat`.

**macOS** – install Python 3 from python.org, then double-click `run_mac.command` (if macOS blocks it: right-click → Open).

**Linux (X11)** – `pip install -r requirements.txt` then `python3 screen_stepper.py` (needs `python3-tk` and `xrandr`).

## Use it

1. **App to capture** – pick a window from the list (↻ refreshes it), or leave it on *"Click the app after pressing Start"* and click the app within 15 seconds after Start.
2. **Then press** – Right arrow, Left, Down, Page Down, Space, Enter, or a mouse right/left-click in the middle of the window.
3. **Wait after each press** – how long to let the app redraw before the next screenshot. If pages get skipped, raise this.
4. Press **Start**. Screen Stepper minimizes itself so it isn't in the shots.

**To stop:** press **Esc**, or just click any other window. It also stops by itself if the window closes, and – if you tick the box – when the page stops changing (the end of a document).

Screenshots go to `Pictures/Screen Stepper/<date time>/0001.png, 0002.png, …`. After stopping, **Open folder** takes you there.

## Make a standalone app

- Windows: double-click `build_windows.bat` → `dist\Screen Stepper.exe`
- macOS: `bash build_mac.sh` → `dist/Screen Stepper.app`

## Permissions & troubleshooting

- **macOS:** the first run asks for **Screen Recording** and **Accessibility** (and possibly **Input Monitoring** for Esc). Allow them for Terminal (or for Screen Stepper.app if you built it) in System Settings → Privacy & Security, then restart the app. Black screenshots = Screen Recording isn't allowed yet.
- **Windows:** if the target app runs as Administrator, run Screen Stepper as Administrator too, otherwise Windows blocks the key presses.
- **It stopped right away with "another window became active":** something popped up over the app (a dialog, a notification). Close it and start again.
- Esc is also delivered to the app you're capturing (e.g. it may leave full-screen mode). Clicking another window is the quieter way to stop.
