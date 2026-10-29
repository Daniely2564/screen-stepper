#!/usr/bin/env python3
"""
Screen Stepper
--------------
Screenshot an app window, press a key (Right arrow by default), screenshot
again, and keep going until you stop it.

Stop at any time by:
  * pressing Esc, or
  * clicking any other window (the app stops as soon as the target loses focus).

Works on Windows, macOS and Linux (X11).
"""

import datetime
import hashlib
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Screen Stepper"
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# On Windows, make the process DPI-aware *before* any window or screen API is
# touched, so window bounds and screenshots use the same (physical) pixels.
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import filedialog, ttk

import mss
import mss.tools

MSS = getattr(mss, "MSS", None) or mss.mss  # mss >= 10 renamed the class
import pywinctl
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

PICK_LABEL = "Click the app after pressing Start"

# label -> (kind, value)
ACTIONS = {
    "→  Right arrow": ("key", Key.right),
    "←  Left arrow": ("key", Key.left),
    "↓  Down arrow": ("key", Key.down),
    "Page Down": ("key", Key.page_down),
    "Space": ("key", Key.space),
    "Enter": ("key", Key.enter),
    "Mouse right-click (center of window)": ("mouse", Button.right),
    "Mouse left-click (center of window)": ("mouse", Button.left),
}
DEFAULT_ACTION = "→  Right arrow"
DEFAULT_FOLDER = Path.home() / "Pictures" / APP_NAME


# --------------------------------------------------------------------------
# Window helpers
# --------------------------------------------------------------------------

def _pid(win):
    try:
        return win.getPID()
    except Exception:
        return None


def is_own_window(win):
    if win is None:
        return False
    pid = _pid(win)
    if pid is not None:
        return pid == os.getpid()
    try:
        return (win.title or "").startswith(APP_NAME)
    except Exception:
        return False


def same_window(a, b):
    if a is None or b is None:
        return False
    if IS_MAC:
        # On macOS window titles can change (e.g. page numbers in the title),
        # so compare by owning application instead.
        pa, pb = _pid(a), _pid(b)
        return pa is not None and pa == pb
    try:
        return a == b
    except Exception:
        return False


def window_title(win):
    try:
        return win.title or "(untitled)"
    except Exception:
        return "(untitled)"


def window_bounds(win):
    """(left, top, width, height) of the window as it appears on screen."""
    if IS_WIN:
        # GetWindowRect includes invisible resize borders on Windows 10/11;
        # the DWM "extended frame bounds" are what you actually see.
        try:
            rect = wintypes.RECT()
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(win.getHandle()), 9,  # DWMWA_EXTENDED_FRAME_BOUNDS
                ctypes.byref(rect), ctypes.sizeof(rect))
            if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except Exception:
            pass
    box = win.box
    return int(box.left), int(box.top), int(box.width), int(box.height)


def list_windows():
    """Visible, titled windows that aren't ours, as (label, window) pairs."""
    items = []
    try:
        windows = pywinctl.getAllWindows()
    except Exception:
        windows = []
    seen = set()
    for w in windows:
        try:
            if is_own_window(w):
                continue
            title = (w.title or "").strip()
            if not title:
                continue
            try:
                if not w.isVisible:
                    continue
            except Exception:
                pass
            app = ""
            if _pid(w) is not None:
                try:
                    app = w.getAppName() or ""
                except Exception:
                    pass
            label = f"{title}  —  {app}" if app and app.lower() not in title.lower() else title
            if len(label) > 90:
                label = label[:87] + "…"
            base, n = label, 2
            while label in seen:
                label = f"{base} ({n})"
                n += 1
            seen.add(label)
            items.append((label, w))
        except Exception:
            continue
    items.sort(key=lambda it: it[0].lower())
    return items


def open_folder(path):
    try:
        if IS_WIN:
            os.startfile(str(path))  # noqa
        elif IS_MAC:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# --------------------------------------------------------------------------
# Esc watcher (global — works while another app has focus)
# --------------------------------------------------------------------------

class EscWatcher:
    def __init__(self, on_escape):
        self.on_escape = on_escape
        self._stop = threading.Event()
        self._listener = None
        self.available = True

    def start(self):
        if IS_WIN or IS_MAC:
            threading.Thread(target=self._poll, daemon=True).start()
        else:
            try:
                from pynput import keyboard

                def on_press(key):
                    if key == keyboard.Key.esc:
                        self.on_escape()

                self._listener = keyboard.Listener(on_press=on_press)
                self._listener.daemon = True
                self._listener.start()
            except Exception:
                self.available = False

    def _poll(self):
        # Polling the key state avoids keyboard hooks, which are fragile when
        # combined with Tk on macOS.
        try:
            if IS_WIN:
                get_state = ctypes.windll.user32.GetAsyncKeyState
                get_state(0x1B)  # clear the "pressed since last call" bit

                def esc_down():
                    return bool(get_state(0x1B) & 0x8000)
            else:
                import Quartz

                def esc_down():
                    return bool(Quartz.CGEventSourceKeyState(
                        Quartz.kCGEventSourceStateHIDSystemState, 53))
        except Exception:
            self.available = False
            return
        while not self._stop.wait(0.025):
            try:
                if esc_down():
                    self.on_escape()
            except Exception:
                self.available = False
                return

    def stop(self):
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass


# --------------------------------------------------------------------------
# The capture loop (runs on a background thread)
# --------------------------------------------------------------------------

class Stopped(Exception):
    """Raised to end the loop with a human-readable reason."""


class Stepper(threading.Thread):
    def __init__(self, target, action, delay, out_dir, whole_screen,
                 stop_when_same, events):
        super().__init__(daemon=True)
        self.target = target
        self.action = action
        self.delay = delay
        self.out_dir = out_dir
        self.whole_screen = whole_screen
        self.stop_when_same = stop_when_same
        self.events = events
        self.stop_event = threading.Event()
        self.stop_reason = None
        self.count = 0
        self.keyboard = KeyboardController()
        self.mouse = MouseController()

    # -- control -----------------------------------------------------------
    def request_stop(self, reason):
        if not self.stop_event.is_set():
            self.stop_reason = reason
            self.stop_event.set()

    def _post(self, *msg):
        self.events.put(msg)

    def _sleep(self, seconds):
        if self.stop_event.wait(seconds):
            raise Stopped(self.stop_reason or "Stopped.")

    # -- main --------------------------------------------------------------
    def run(self):
        reason = None
        try:
            self._run()
        except Stopped as s:
            reason = str(s)
        except Exception as e:  # keep the GUI alive whatever happens
            reason = f"Stopped because of an error: {e}"
        self._post("done", self.count, reason or self.stop_reason or "Stopped.",
                   self.out_dir if self.count else None)

    def _active(self):
        try:
            return pywinctl.getActiveWindow()
        except Exception:
            return None

    def _wait_for_pick(self, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            win = self._active()
            if win is not None and not is_own_window(win):
                return win
            self._sleep(0.15)
        return None

    def _region(self, sct, bounds):
        left, top, width, height = bounds
        if self.whole_screen:
            cx, cy = left + width // 2, top + height // 2
            for mon in sct.monitors[1:]:
                if (mon["left"] <= cx < mon["left"] + mon["width"]
                        and mon["top"] <= cy < mon["top"] + mon["height"]):
                    return dict(mon)
            return dict(sct.monitors[1])
        # Clip to the desktop so partly off-screen windows still work.
        desk = sct.monitors[0]
        x1 = max(left, desk["left"])
        y1 = max(top, desk["top"])
        x2 = min(left + width, desk["left"] + desk["width"])
        y2 = min(top + height, desk["top"] + desk["height"])
        if x2 - x1 < 2 or y2 - y1 < 2:
            raise Stopped("The window is off-screen.")
        return {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}

    def _do_action(self, bounds):
        kind, value = self.action
        if kind == "key":
            self.keyboard.press(value)
            time.sleep(0.03)
            self.keyboard.release(value)
        else:
            left, top, width, height = bounds
            self.mouse.position = (left + width // 2, top + height // 2)
            time.sleep(0.05)
            self.mouse.click(value)

    def _run(self):
        target = self.target
        if target is None:
            self._post("status", "Click the app you want to capture…")
            target = self._wait_for_pick()
            if target is None:
                raise Stopped("No app was clicked within 15 seconds.")
        title = window_title(target)
        self._post("locked", title)

        # Give our own window time to minimise, then bring the target forward.
        self._sleep(0.8)
        try:
            if target.isMinimized:
                target.restore()
        except Exception:
            pass
        try:
            target.activate()
        except Exception:
            pass
        self._sleep(1.0)
        if not same_window(self._active(), target):
            try:
                target.activate()
            except Exception:
                pass
            self._sleep(0.7)
            if not same_window(self._active(), target):
                raise Stopped(f"Couldn't bring “{title}” to the front. "
                              "Click it yourself and try again.")

        self.out_dir.mkdir(parents=True, exist_ok=True)
        bounds = window_bounds(target)
        last_digest = None
        same_streak = 0

        with MSS() as sct:
            while True:
                if self.stop_event.is_set():
                    raise Stopped(self.stop_reason)

                # Safety: only ever press keys into the target app.
                active = self._active()
                if active is not None and not same_window(active, target):
                    raise Stopped("Stopped — another window became active.")

                try:
                    bounds = window_bounds(target)
                except Exception:
                    pass  # keep the last known position

                shot = sct.grab(self._region(sct, bounds))
                digest = hashlib.blake2b(shot.raw, digest_size=16).digest()
                if digest == last_digest:
                    same_streak += 1
                else:
                    same_streak = 0
                last_digest = digest

                if self.stop_when_same and same_streak:
                    if same_streak >= 2:
                        raise Stopped("Stopped — the page stopped changing.")
                else:
                    self.count += 1
                    path = self.out_dir / f"{self.count:04d}.png"
                    mss.tools.to_png(shot.rgb, shot.size, output=str(path))
                    self._post("shot", self.count)

                if self.stop_event.is_set():
                    raise Stopped(self.stop_reason)
                self._do_action(bounds)
                self._sleep(self.delay)


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.esc = None
        self.window_map = {}
        self.last_folder = None

        root.title(APP_NAME)
        root.resizable(False, False)
        style = ttk.Style(root)
        if not (IS_WIN or IS_MAC):
            style.theme_use("clam")

        pad = {"padx": 12, "pady": 5}
        main = ttk.Frame(root, padding=(8, 10, 8, 8))
        main.grid(sticky="nsew")
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="App to capture").grid(row=0, column=0, sticky="w", **pad)
        self.target_var = tk.StringVar(value=PICK_LABEL)
        self.target_box = ttk.Combobox(main, textvariable=self.target_var,
                                       state="readonly", width=44)
        self.target_box.grid(row=0, column=1, sticky="ew", pady=5)
        self.refresh_btn = ttk.Button(main, text="↻", width=3, command=self.refresh_windows)
        self.refresh_btn.grid(row=0, column=2, padx=(6, 12))

        ttk.Label(main, text="Then press").grid(row=1, column=0, sticky="w", **pad)
        self.action_var = tk.StringVar(value=DEFAULT_ACTION)
        self.action_box = ttk.Combobox(main, textvariable=self.action_var, state="readonly",
                                       values=list(ACTIONS), width=44)
        self.action_box.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(main, text="Wait after each press").grid(row=2, column=0, sticky="w", **pad)
        delay_row = ttk.Frame(main)
        delay_row.grid(row=2, column=1, sticky="w", pady=5)
        self.delay_var = tk.StringVar(value="1.0")
        self.delay_spin = ttk.Spinbox(delay_row, from_=0.1, to=60, increment=0.1,
                                      textvariable=self.delay_var, width=6, format="%.1f")
        self.delay_spin.pack(side="left")
        ttk.Label(delay_row, text="seconds").pack(side="left", padx=6)

        ttk.Label(main, text="Capture").grid(row=3, column=0, sticky="w", **pad)
        cap_row = ttk.Frame(main)
        cap_row.grid(row=3, column=1, sticky="w", pady=5)
        self.whole_var = tk.BooleanVar(value=False)
        self.cap_win = ttk.Radiobutton(cap_row, text="Just the window", variable=self.whole_var, value=False)
        self.cap_win.pack(side="left")
        self.cap_screen = ttk.Radiobutton(cap_row, text="Whole screen", variable=self.whole_var, value=True)
        self.cap_screen.pack(side="left", padx=(14, 0))

        ttk.Label(main, text="Save to").grid(row=4, column=0, sticky="w", **pad)
        self.folder_var = tk.StringVar(value=str(DEFAULT_FOLDER))
        self.folder_entry = ttk.Entry(main, textvariable=self.folder_var, width=46)
        self.folder_entry.grid(row=4, column=1, sticky="ew", pady=5)
        self.browse_btn = ttk.Button(main, text="…", width=3, command=self.browse)
        self.browse_btn.grid(row=4, column=2, padx=(6, 12))

        self.same_var = tk.BooleanVar(value=False)
        self.same_check = ttk.Checkbutton(
            main, text="Stop by itself when the page stops changing (end of document)",
            variable=self.same_var)
        self.same_check.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 4))

        ttk.Separator(main).grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=8)

        bottom = ttk.Frame(main)
        bottom.grid(row=7, column=0, columnspan=3, sticky="ew", padx=12)
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Stop any time: press Esc or click another window.")
        ttk.Label(bottom, textvariable=self.status_var, wraplength=420,
                  justify="left").grid(row=0, column=0, sticky="w")
        self.open_btn = ttk.Button(bottom, text="Open folder", command=self.open_last)
        self.start_btn = ttk.Button(bottom, text="Start", command=self.toggle)
        self.start_btn.grid(row=0, column=2, padx=(8, 0))

        self.inputs = [self.target_box, self.refresh_btn, self.action_box, self.delay_spin,
                       self.cap_win, self.cap_screen, self.folder_entry, self.browse_btn,
                       self.same_check]

        root.bind("<Return>", lambda e: self.toggle())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_windows()
        root.after(100, self.poll_events)

    # -- inputs ------------------------------------------------------------
    def refresh_windows(self):
        items = list_windows()
        self.window_map = dict(items)
        current = self.target_var.get()
        self.target_box["values"] = [PICK_LABEL] + [label for label, _ in items]
        if current not in self.window_map:
            self.target_var.set(PICK_LABEL)

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def set_inputs(self, enabled):
        for w in self.inputs:
            if isinstance(w, ttk.Combobox):
                w.configure(state="readonly" if enabled else "disabled")
            else:
                w.configure(state="normal" if enabled else "disabled")

    # -- run ---------------------------------------------------------------
    def toggle(self):
        if self.worker is not None:
            self.stop("Stopped.")
        else:
            self.start()

    def start(self):
        try:
            delay = float(self.delay_var.get())
            if not 0.05 <= delay <= 600:
                raise ValueError
        except ValueError:
            self.status_var.set("Wait time should be a number of seconds, e.g. 1.0")
            return

        target = None
        label = self.target_var.get()
        if label != PICK_LABEL:
            target = self.window_map.get(label)
            alive = False
            try:
                alive = target is not None and target.isAlive
            except Exception:
                pass
            if not alive:
                self.refresh_windows()
                self.status_var.set("That window is gone — pick it again.")
                return

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        out_dir = Path(self.folder_var.get().strip() or DEFAULT_FOLDER).expanduser() / stamp

        self.open_btn.grid_forget()
        self.set_inputs(False)
        self.start_btn.configure(text="Stop")
        self.worker = Stepper(target, ACTIONS[self.action_var.get()], delay, out_dir,
                              self.whole_var.get(), self.same_var.get(), self.events)
        self.esc = EscWatcher(lambda: self.worker and self.worker.request_stop("Stopped (Esc)."))
        self.esc.start()
        if target is not None:
            self.status_var.set(f"Starting on “{window_title(target)}”…")
        self.worker.start()

    def stop(self, reason):
        if self.worker is not None:
            self.worker.request_stop(reason)

    def poll_events(self):
        try:
            while True:
                msg = self.events.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status_var.set(msg[1])
                elif kind == "locked":
                    self.status_var.set(f"Capturing “{msg[1]}”… press Esc to stop.")
                    self.root.iconify()
                elif kind == "shot":
                    self.root.title(f"{APP_NAME} — {msg[1]} shots")
                elif kind == "done":
                    self.finish(*msg[1:])
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def finish(self, count, reason, folder):
        if self.esc is not None:
            self.esc.stop()
            self.esc = None
        self.worker = None
        self.root.title(APP_NAME)
        self.set_inputs(True)
        self.start_btn.configure(text="Start")
        if count:
            self.last_folder = folder
            noun = "screenshot" if count == 1 else "screenshots"
            self.status_var.set(f"{reason}\nSaved {count} {noun} to:\n{folder}")
            self.open_btn.grid(row=0, column=1, padx=(8, 0))
        else:
            self.status_var.set(reason)
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def open_last(self):
        if self.last_folder:
            open_folder(self.last_folder)

    def on_close(self):
        self.stop("Closed.")
        if self.esc is not None:
            self.esc.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
