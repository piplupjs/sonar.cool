from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .analyzer import Analyzer, Reading
from .gestures import (
    ZOOM_SCALES,
    DoublePushDetector,
    ScrollMotion,
    SystemScrollState,
    WaveCalibration,
    ZoomMotion,
    zoom_keys,
)
from .ranging import PlanePosition, RangeAnalyzer, RangeFrame, RangeReading
from .windows_input import foreground_is_browser, post_scroll, send_arrow, send_zoom_keys


MODES = ("scroll", "gallery", "zoom", "signal", "distance", "position")


@dataclass
class SignalFrame:
    time: float
    power: list[float]
    spectrum: list[float]
    waveform: list[float]
    sample_rate: float
    first_frequency: float
    bin_width: float
    carrier_db: float


class SignalHistory:
    def __init__(self) -> None:
        self.frames: list[SignalFrame] = []

    def append(self, reading: Reading, now: float) -> None:
        last = self.frames[-1].time if self.frames else -math.inf
        if now - last < 0.055:
            return
        if not reading.spectrum or len(reading.spectrum) != len(reading.baseline):
            return
        center = len(reading.spectrum) // 2
        carrier = 10.0 ** (reading.carrier_db / 10.0)
        values = []
        for i, db in enumerate(reading.spectrum):
            if abs(i - center) < 3:
                values.append(0.0)
                continue
            residual = max(0.0, 10.0 ** (db / 10.0) - 2.0 * 10.0 ** (reading.baseline[i] / 10.0))
            values.append(min(1.0, math.log1p(residual / max(carrier, 1e-12) / 0.0001) / 8.0))
        self.frames.append(
            SignalFrame(
                now,
                values,
                reading.spectrum,
                reading.waveform,
                reading.sample_rate,
                reading.first_frequency,
                reading.bin_width,
                reading.carrier_db,
            )
        )
        self.frames = [frame for frame in self.frames if now - frame.time <= 5]
        if len(self.frames) > 90:
            self.frames = self.frames[-90:]

    def clear(self) -> None:
        self.frames = []


@dataclass
class SessionState:
    mode: str = "scroll"
    running: bool = False
    starting: bool = False
    status: str = "Ready — sound is off"
    route: str = "Built-in speakers + microphone • Bluetooth excluded"
    frequency: float = 20000.0
    level: float = 0.008
    calibration_remaining: float | None = None
    action: str = "Lift to scroll · lower to reset"
    forward: bool = True
    air_tap_enabled: bool = True
    gesture_feedback: str = ""
    gallery_index: int = 0
    gallery_count: int = 5
    gallery_reversed: bool = False
    last_navigation: str = "↔"
    zoom_scale: float = 1.0
    zoom_reversed: bool = False
    zoom_indicator: str = "100%"
    zoom_feedback: str = "Open a photo or page in another app, then Start."
    control_other_apps: bool = False
    reading: Reading | None = None
    distance_cm: float | None = None
    distance_status: str = "Start to see live echoes"
    distance_frames: list[RangeFrame] = field(default_factory=list)
    left_cm: float | None = None
    right_cm: float | None = None
    position_x: float | None = None
    position_y: float | None = None
    signal_frames: list[SignalFrame] = field(default_factory=list)
    photos: list[Path] = field(default_factory=list)
    scroll_offset: float = 0.0


class SonarSession:
    def __init__(self, assets: Path, on_change: Callable[[], None] | None = None) -> None:
        self.assets = assets
        self.on_change = on_change or (lambda: None)
        self.state = SessionState()
        self.state.photos = self._sample_photos()
        self.state.gallery_count = max(1, len(self.state.photos))
        self.wave = WaveCalibration(reversed_mapping=False)
        self.state.gallery_reversed = self.wave.reversed
        self.motion = ScrollMotion()
        self.taps = DoublePushDetector()
        self.zoom = ZoomMotion()
        self.scroll = SystemScrollState()
        self.signal = SignalHistory()
        self._engine = None
        self._lock = threading.Lock()
        self._session = 0
        self._last_tick = time.monotonic()
        self._zoom_steps_sent = 0
        self._scroll_offset = 0.0
        self._pending: list = []
        self._analysis_thread: threading.Thread | None = None
        self._analysis_stop = threading.Event()

    def _sample_photos(self) -> list[Path]:
        gallery = self.assets / "gallery"
        return [gallery / f"{index:02d}.jpg" for index in range(1, 6) if (gallery / f"{index:02d}.jpg").exists()]

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self.state.mode:
            return
        self.stop()
        self.state.mode = mode
        self.wave.cancel()
        self._reset_motion()
        self.on_change()

    def start(self) -> None:
        if self.state.running or self.state.starting:
            return
        self.state.starting = True
        self.state.status = "Starting…"
        self.on_change()
        try:
            from .audio import HardwareAudio, pick_devices

            _in_id, _out_id, route = pick_devices()
            self.state.route = route
            ranging = self.state.mode in ("distance", "position")
            positioning = self.state.mode == "position"
            engine = HardwareAudio(
                self.state.frequency,
                self.state.level,
                self._receive,
                ranging=ranging,
                positioning=positioning,
            )
            engine.start()
            self._engine = engine
            self._session += 1
            self._prepare_analyzers(engine.input_rate)
            self._analysis_stop.clear()
            self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
            self._analysis_thread.start()
            self.state.running = True
            self.state.starting = False
            self.state.calibration_remaining = 3.0 if ranging else 2.5
            self.state.status = "Calibrating — hands still"
            self.wave.live = True
            self.signal.clear()
            self._last_tick = time.monotonic()
            self.on_change()
        except Exception as error:
            self.stop()
            self.state.status = f"Could not start: {error}"
            self.on_change()

    def stop(self) -> None:
        engine = self._engine
        self._engine = None
        self._analysis_stop.set()
        if engine is not None:
            engine.stop()
        self._session += 1
        with self._lock:
            self._pending = []
        self.state.running = False
        self.state.starting = False
        self.state.calibration_remaining = None
        self.state.status = "Stopped — sound is off"
        self.wave.live = False
        self.wave.cancel()
        self._reset_motion()
        self.on_change()

    def _prepare_analyzers(self, rate: float) -> None:
        self._rate = rate
        self._samples = np.zeros(0, dtype=np.float32)
        self._analyzer = Analyzer(rate, self.state.frequency)
        self._range = RangeAnalyzer(rate) if self.state.mode in ("distance", "position") else None
        self._right = RangeAnalyzer(rate, descending=True) if self.state.mode == "position" else None
        self._distance_frames: list[RangeFrame] = []
        self._pending = []

    def _receive(self, block) -> None:
        with self._lock:
            if not self.state.running:
                return
            self._pending.append(np.asarray(block, dtype=np.float32))
            if len(self._pending) > 48:
                self._pending = self._pending[-24:]

    def _analysis_loop(self) -> None:
        session = self._session
        while not self._analysis_stop.is_set() and session == self._session:
            with self._lock:
                chunks = self._pending
                self._pending = []
            if chunks:
                self._samples = np.concatenate([self._samples, *chunks]) if self._samples.size else np.concatenate(chunks)
                if self._range is not None:
                    while self._samples.size >= self._range.n:
                        window = self._samples[: self._range.n]
                        left = self._range.analyze(window)
                        right = self._right.analyze(window) if self._right else None
                        self._samples = self._samples[self._range.hop :]
                        if self._session != session:
                            return
                        self._apply_range(left, right)
                else:
                    while self._samples.size >= self._analyzer.n:
                        reading = self._analyzer.analyze(self._samples[: self._analyzer.n])
                        self._samples = self._samples[self._analyzer.hop :]
                        if self._session != session:
                            return
                        self._apply_reading(reading)
                if self._samples.size > 96000:
                    self._samples = self._samples[-16384:]
            else:
                time.sleep(0.005)

    def _apply_range(self, left: RangeReading, right: RangeReading | None) -> None:
        now = time.monotonic()
        self.state.status = left.status
        self.state.calibration_remaining = left.calibration_remaining
        if right is None:
            self.state.distance_cm = left.cm
            self.state.distance_status = left.status
            self._distance_frames.append(RangeFrame(now, left))
            self._distance_frames = [frame for frame in self._distance_frames if now - frame.time <= 5][-90:]
            self.state.distance_frames = list(self._distance_frames)
        else:
            self.state.left_cm = left.cm
            self.state.right_cm = right.cm
            point = None
            if left.cm is not None and right.cm is not None:
                point = PlanePosition.solve(left.cm, right.cm, 24.0)
            self.state.position_x = point.x if point else None
            self.state.position_y = point.height if point else None
        self.on_change()

    def _apply_reading(self, reading: Reading) -> None:
        now = time.monotonic()
        self.state.reading = reading
        self.state.status = reading.direction
        self.state.calibration_remaining = reading.calibration_remaining
        self.consume(reading, now)
        self._tick(now)
        self.state.signal_frames = list(self.signal.frames)
        self.on_change()

    def consume(self, reading: Reading, now: float) -> None:
        mode = self.state.mode
        if mode == "zoom":
            action = self.zoom.feed(reading, now, self.state.zoom_reversed)
            if action is None:
                return
            if self.state.control_other_apps:
                keys = zoom_keys(action, foreground_is_browser(), self._zoom_steps_sent)
                if send_zoom_keys(keys):
                    if action > 0:
                        self._zoom_steps_sent += 3
                    elif action < 0:
                        self._zoom_steps_sent = max(0, self._zoom_steps_sent - 1)
                    else:
                        self._zoom_steps_sent = 0
            self.zoom.steps = max(0, min(3, self.zoom.steps))
            self.state.zoom_scale = ZOOM_SCALES[self.zoom.steps]
            self.state.zoom_indicator = "+" if action > 0 else "−"
            if action > 0:
                self.state.zoom_feedback = "Zoomed in · pull back to reset"
            elif action == 0:
                self.state.zoom_feedback = "Zoom return sent · push to zoom"
            else:
                self.state.zoom_feedback = "Zooming out · keep pulling back"
            return
        if mode == "signal":
            self.signal.append(reading, now)
            return
        if mode == "gallery":
            event = self.wave.consume(reading, now)
            if event:
                self._gallery_event(event, from_gesture=True)
            return
        self.motion.feed(reading.direction, reading.strength, now)
        if self.state.air_tap_enabled and self.taps.feed(reading.direction, now):
            self.motion.switch_direction(now)
        self.state.forward = self.motion.forward
        if self.state.air_tap_enabled and now < self.taps.feedback_until:
            self.state.gesture_feedback = self.taps.feedback
        else:
            self.state.gesture_feedback = ""

    def _tick(self, now: float) -> None:
        dt = min(1 / 30, max(0.0, now - self._last_tick))
        self._last_tick = now
        if self.state.mode != "scroll":
            return
        delta = self.motion.step(dt, now)
        if abs(delta) > 0.01:
            if self.state.control_other_apps:
                pixels = self.scroll.pixels(delta)
                post_scroll(pixels)
            else:
                self.scroll_practice(delta)
        velocity = self.motion.velocity
        if self.state.air_tap_enabled and now < self.taps.feedback_until:
            next_action = self.taps.feedback
        elif velocity > 10:
            next_action = "↓ Scrolling down"
        elif velocity < -10:
            next_action = "↑ Scrolling up"
        else:
            next_action = "Lift to scroll · lower to reset"
        self.state.action = next_action

    def _gallery_event(self, event: str, from_gesture: bool) -> None:
        if self.state.control_other_apps and from_gesture and send_arrow(event == "next"):
            self.state.last_navigation = "→" if event == "next" else "←"
            return
        count = max(1, len(self.state.photos) or self.state.gallery_count)
        if event == "next":
            self.state.gallery_index = (self.state.gallery_index + 1) % count
        else:
            self.state.gallery_index = (self.state.gallery_index + count - 1) % count
        self.state.last_navigation = "→" if event == "next" else "←"

    def scroll_practice(self, points: float) -> None:
        self._scroll_offset = min(4000.0, max(0.0, self._scroll_offset + points))
        self.state.scroll_offset = self._scroll_offset

    def practice_scroll(self, points: float) -> None:
        self.scroll_practice(points)
        self.on_change()

    def practice_gallery(self, next_image: bool) -> None:
        self._gallery_event("next" if next_image else "previous", from_gesture=False)
        self.on_change()

    def practice_zoom(self, action: int) -> None:
        if action > 0:
            self.zoom.steps = 3
        elif action == 0:
            self.zoom.steps = 0
        else:
            self.zoom.steps = max(0, self.zoom.steps - 1)
        self.state.zoom_scale = ZOOM_SCALES[self.zoom.steps]
        self.state.zoom_indicator = "+" if action > 0 else "−"
        self.on_change()

    def switch_direction(self) -> None:
        self.state.forward = not self.state.forward
        self._reset_motion()
        self.on_change()

    def set_gallery_reversed(self, value: bool) -> None:
        self.wave.reversed = value
        self.state.gallery_reversed = value
        self.wave.reset_input()
        self.on_change()

    def _reset_motion(self) -> None:
        forward = self.state.forward
        toggles = self.motion.toggles
        self.motion = ScrollMotion()
        self.motion.forward = forward
        self.motion.toggles = toggles
        self.taps = DoublePushDetector()
        self.scroll.reset()
        self.state.gesture_feedback = ""
        self.state.action = "Lift to scroll · lower to reset"
        self.state.scroll_offset = self._scroll_offset
