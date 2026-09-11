from __future__ import annotations

import sys
import threading
from pathlib import Path

from .session import SonarSession

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "assets"
LABELS = {
    "scroll": "Scroll",
    "gallery": "Swipe",
    "zoom": "Zoom",
    "signal": "Signal",
    "distance": "Distance",
    "position": "Position",
}
SUBTITLES = {
    "scroll": "Scroll through a page without touching the computer.",
    "gallery": "Browse photos by moving your hand left and right.",
    "zoom": "Take a closer look by moving your hand.",
    "signal": "Watch changes in the microphone signal.",
    "distance": "Explore experimental echo-delay estimates.",
    "position": "An experimental estimate from two speaker echoes.",
}
INSTRUCTIONS = {
    "scroll": "Lift your hand up and down to scroll. Do a double tap (in the air!) to reverse directions.",
    "gallery": "Sweep your palm sideways above the keyboard to browse. Pause briefly between sweeps.",
    "zoom": "Push toward the screen to zoom in and pull back to zoom out.",
    "signal": "Watch the live Doppler signal as you move your hand.",
    "distance": "Measure an experimental echo range with swept tones.",
    "position": "Test independent echoes from both speakers.",
}


def repo_assets() -> Path:
    if ASSETS.exists():
        return ASSETS
    return Path(__file__).resolve().parents[1] / "assets"


class SonarApp:
    def __init__(self, root) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.root = root
        self.root.title("Sonar")
        self.root.minsize(960, 640)
        self.session = SonarSession(repo_assets(), on_change=self._schedule_refresh)
        self._busy = False
        self._photo = None
        self._stop_was_down = False
        self._flip_was_down = False
        self._controls_mode = None
        self._build()
        self.root.bind("<Escape>", lambda _e: self.session.stop())
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(50, self._poll_hotkeys)
        self._refresh()

    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self.root, padding=16)
        sidebar.grid(row=0, column=0, sticky="nsw")
        ttk.Label(sidebar, text="sonar", font=("Arial", 22, "bold"), foreground="#007aff").pack(anchor="w")
        ttk.Label(sidebar, text="CONTROLS", padding=(0, 16, 0, 4)).pack(anchor="w")
        self.mode_var = tk.StringVar(value=self.session.state.mode)
        for mode in ("scroll", "gallery", "zoom"):
            ttk.Radiobutton(
                sidebar, text=LABELS[mode], value=mode, variable=self.mode_var, command=self._mode_changed
            ).pack(anchor="w")
        ttk.Label(sidebar, text="EXPERIMENTS", padding=(0, 16, 0, 4)).pack(anchor="w")
        for mode in ("signal", "distance", "position"):
            ttk.Radiobutton(
                sidebar, text=LABELS[mode], value=mode, variable=self.mode_var, command=self._mode_changed
            ).pack(anchor="w")
        ttk.Label(sidebar, text="Built-in audio", padding=(0, 18, 0, 4)).pack(anchor="w")
        ttk.Button(sidebar, text="Audio settings…", command=self._audio_settings).pack(anchor="w")
        ttk.Label(sidebar, text="Version 0.1.0 (Windows)", padding=(0, 12, 0, 0)).pack(anchor="w")
        ttk.Label(sidebar, text="Stop anywhere  Ctrl+Alt+Win+Space").pack(anchor="w")

        main = ttk.Frame(self.root, padding=24)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)
        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        self.start_btn = ttk.Button(top, text="Start", command=self._toggle)
        self.start_btn.pack(side="left")
        self.title_label = ttk.Label(top, text="Scroll", font=("Segoe UI", 22, "bold"))
        self.title_label.pack(side="left", padx=16)
        self.status_dot = ttk.Label(top, text="Stopped")
        self.status_dot.pack(side="right")
        self.countdown = ttk.Label(main, text="")
        self.countdown.grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.instruction = ttk.Label(main, text=INSTRUCTIONS["scroll"], wraplength=640)
        self.instruction.grid(row=2, column=0, sticky="w", pady=(16, 8))
        self.subtitle = ttk.Label(main, text=SUBTITLES["scroll"], wraplength=640)
        self.subtitle.grid(row=3, column=0, sticky="w")
        self.stage = ttk.Frame(main, relief="solid")
        self.stage.grid(row=4, column=0, sticky="nsew", pady=16)
        self.stage.columnconfigure(0, weight=1)
        self.stage.rowconfigure(0, weight=1)
        self.preview = tk.Text(self.stage, wrap="word", height=12)
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.image_label = ttk.Label(self.stage)
        controls = ttk.Frame(main)
        controls.grid(row=5, column=0, sticky="ew")
        self.left_btn = ttk.Button(controls, text="Previous", command=lambda: self.session.practice_gallery(False))
        self.right_btn = ttk.Button(controls, text="Next", command=lambda: self.session.practice_gallery(True))
        self.dir_btn = ttk.Button(controls, text="Direction: ↓", command=self.session.switch_direction)
        self.zoom_in_btn = ttk.Button(controls, text="Zoom in", command=lambda: self.session.practice_zoom(3))
        self.zoom_reset_btn = ttk.Button(controls, text="Reset", command=lambda: self.session.practice_zoom(0))
        self.scroll_up_btn = ttk.Button(controls, text="Scroll up", command=lambda: self.session.practice_scroll(-180))
        self.scroll_down_btn = ttk.Button(controls, text="Scroll down", command=lambda: self.session.practice_scroll(180))
        self.air_tap = tk.BooleanVar(value=True)
        self.reverse = tk.BooleanVar(value=False)
        self.other_apps = tk.BooleanVar(value=False)
        self.air_tap_btn = ttk.Checkbutton(controls, text="Air double-tap", variable=self.air_tap, command=self._options)
        self.reverse_btn = ttk.Checkbutton(controls, text="Reverse directions", variable=self.reverse, command=self._options)
        self.other_apps_btn = ttk.Checkbutton(controls, text="Control other apps", variable=self.other_apps, command=self._options)
        self.other_apps_btn.pack(side="right")
        self.reverse_btn.pack(side="right", padx=8)
        self.air_tap_btn.pack(side="right")
        self.feedback = ttk.Label(main, text="Ready when you are")
        self.feedback.grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Label(main, text="Microphone audio stays on this computer.").grid(row=7, column=0, sticky="w", pady=(8, 0))
        self._fill_practice_text()

    def _fill_practice_text(self) -> None:
        pages = [
            "Sonar — practice reading",
            "",
            "Lift your palm to scroll. Lower it to reset.",
            "Two short downward pushes switch direction.",
            "Try a slow movement, then pause. You can stop any time.",
            "This is an experiment. Different PCs and rooms may respond differently.",
        ]
        self.preview.delete("1.0", "end")
        for _ in range(8):
            self.preview.insert("end", "\n".join(pages) + "\n\n")

    def _mode_changed(self) -> None:
        self.session.set_mode(self.mode_var.get())
        self._refresh()

    def _options(self) -> None:
        self.session.state.air_tap_enabled = self.air_tap.get()
        if self.session.state.mode == "gallery":
            self.session.set_gallery_reversed(self.reverse.get())
        elif self.session.state.mode == "zoom":
            self.session.state.zoom_reversed = self.reverse.get()
        self.session.state.control_other_apps = self.other_apps.get()

    def _toggle(self) -> None:
        if self.session.state.running or self.session.state.starting:
            self.session.stop()
        else:
            threading.Thread(target=self.session.start, daemon=True).start()

    def _audio_settings(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        win = tk.Toplevel(self.root)
        win.title("Audio settings")
        win.geometry("420x280")
        ttk.Label(win, text="Use the built-in speakers and microphone. Stop Sonar before changing the tone.", wraplength=380).pack(anchor="w", padx=16, pady=12)
        freq = tk.DoubleVar(value=self.session.state.frequency)
        level = tk.DoubleVar(value=self.session.state.level * 100)
        ttk.Label(win, text="Frequency").pack(anchor="w", padx=16)
        for value in (18000.0, 19000.0, 20000.0, 21000.0):
            ttk.Radiobutton(win, text=f"{int(value/1000)} kHz", value=value, variable=freq).pack(anchor="w", padx=24)
        ttk.Label(win, text="Signal level").pack(anchor="w", padx=16, pady=(12, 0))
        ttk.Scale(win, from_=0.2, to=4.0, variable=level, orient="horizontal").pack(fill="x", padx=16)
        ttk.Label(win, text=self.session.state.route, wraplength=360).pack(anchor="w", padx=16, pady=8)

        def apply() -> None:
            if not self.session.state.running:
                self.session.state.frequency = freq.get()
                self.session.state.level = max(0.002, min(0.04, level.get() / 100.0))
            win.destroy()

        ttk.Button(win, text="Done", command=apply).pack(pady=16)

    def _schedule_refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self.root.after(0, self._refresh)
        except Exception:
            pass

    def _refresh(self) -> None:
        self._busy = False
        state = self.session.state
        self.title_label.config(text=LABELS[state.mode])
        self.instruction.config(text=INSTRUCTIONS[state.mode])
        self.subtitle.config(text=SUBTITLES[state.mode])
        running = state.running or state.starting
        self.start_btn.config(text="Stop" if running else "Start")
        if state.starting:
            live = "Starting"
        elif state.running:
            live = "Calibrating" if state.calibration_remaining else "Active"
        else:
            live = "Stopped"
        self.status_dot.config(text=live)
        if state.calibration_remaining is not None:
            self.countdown.config(text=f"Calibrating — keep still  {max(1, int(state.calibration_remaining + 0.999))}")
        else:
            self.countdown.config(text="")
        self.dir_btn.config(text="Direction: ↓" if state.forward else "Direction: ↑")
        if self._controls_mode != state.mode:
            self._layout_controls(state.mode)
            self._controls_mode = state.mode
        if state.mode == "scroll":
            try:
                self.preview.yview_moveto(min(1.0, state.scroll_offset / 4000.0))
            except Exception:
                pass
        if state.mode == "scroll":
            self.feedback.config(text=state.action if state.running else state.status)
        elif state.mode == "gallery":
            self.feedback.config(text=self.session.wave.message if state.running else state.status)
        elif state.mode == "zoom":
            self.feedback.config(text=state.zoom_feedback if state.running else state.status)
        elif state.mode == "distance":
            cm = f"≈ {state.distance_cm:.0f} cm" if state.distance_cm is not None else "— cm"
            self.feedback.config(text=f"{cm}  {state.distance_status}")
        elif state.mode == "position":
            point = (
                f"x {state.position_x:.0f} · y {state.position_y:.0f} cm"
                if state.position_x is not None and state.position_y is not None
                else "No position estimate"
            )
            self.feedback.config(text=point)
        else:
            extra = ""
            if state.reading is not None:
                extra = f"  carrier {state.reading.carrier_db:.0f} dB"
            self.feedback.config(text=state.status + extra)
        show_text = state.mode == "scroll"
        if show_text:
            self.preview.grid(row=0, column=0, sticky="nsew")
            self.image_label.grid_forget()
        else:
            self.preview.grid_forget()
            self.image_label.grid(row=0, column=0, sticky="nsew")
            self._show_photo()

    def _layout_controls(self, mode: str) -> None:
        for widget in (
            self.left_btn,
            self.right_btn,
            self.dir_btn,
            self.scroll_up_btn,
            self.scroll_down_btn,
            self.zoom_in_btn,
            self.zoom_reset_btn,
            self.air_tap_btn,
            self.reverse_btn,
        ):
            widget.pack_forget()
        if mode == "scroll":
            self.scroll_up_btn.pack(side="left")
            self.scroll_down_btn.pack(side="left", padx=6)
            self.dir_btn.pack(side="left")
            self.air_tap_btn.pack(side="right")
        elif mode == "gallery":
            self.left_btn.pack(side="left")
            self.right_btn.pack(side="left", padx=6)
            self.reverse_btn.pack(side="right")
        elif mode == "zoom":
            self.zoom_in_btn.pack(side="left")
            self.zoom_reset_btn.pack(side="left", padx=6)
            self.reverse_btn.pack(side="right")
        self.reverse_btn.config(text="Reverse gestures" if mode == "zoom" else "Reverse directions")

    def _poll_hotkeys(self) -> None:
        from .windows_input import flip_shortcut_pressed, stop_shortcut_pressed

        stop_down = stop_shortcut_pressed()
        flip_down = flip_shortcut_pressed()
        if stop_down and not self._stop_was_down:
            self.session.stop()
        if flip_down and not self._flip_was_down:
            self.session.switch_direction()
        self._stop_was_down = stop_down
        self._flip_was_down = flip_down
        self.root.after(50, self._poll_hotkeys)

    def _show_photo(self) -> None:
        state = self.session.state
        path = None
        if state.mode == "gallery" and state.photos:
            path = state.photos[state.gallery_index % len(state.photos)]
        elif state.mode == "zoom":
            candidate = repo_assets() / "zoom" / "yoda.jpeg"
            if candidate.exists():
                path = candidate
        if path is None:
            self.image_label.config(text=state.status, image="")
            return
        try:
            from PIL import Image, ImageTk

            image = Image.open(path)
            image.thumbnail((560, 320))
            if state.mode == "zoom":
                width = max(1, int(image.width * state.zoom_scale))
                height = max(1, int(image.height * state.zoom_scale))
                image = image.resize((width, height))
            self._photo = ImageTk.PhotoImage(image)
            self.image_label.config(image=self._photo, text="")
        except Exception:
            self.image_label.config(text=path.name, image="")

    def _close(self) -> None:
        self.session.stop()
        self.root.destroy()


def main() -> None:
    if "--self-test" in sys.argv:
        from .tests_runner import run_self_test

        run_self_test()
        return
    import tkinter as tk

    root = tk.Tk()
    SonarApp(root)
    root.mainloop()
