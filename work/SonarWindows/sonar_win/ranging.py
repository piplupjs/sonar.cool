from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


class RangePulse:
    duration = 0.006
    period = 0.060
    low = 18000.0
    high = 21000.0

    @staticmethod
    def window(t: float) -> float:
        edge = min(t, RangePulse.duration - t)
        if edge < 0.0003:
            return 0.5 - 0.5 * math.cos(math.pi * max(0.0, edge) / 0.0003)
        return 1.0

    @staticmethod
    def sample(time: float, descending: bool = False) -> float:
        t = time % RangePulse.period
        if t < 0 or t >= RangePulse.duration:
            return 0.0
        start = RangePulse.high if descending else RangePulse.low
        sweep = RangePulse.low - RangePulse.high if descending else RangePulse.high - RangePulse.low
        phase = 2.0 * math.pi * (start * t + sweep * t * t / (2.0 * RangePulse.duration))
        return math.sin(phase) * RangePulse.window(t)

    @staticmethod
    def samples(times, descending: bool = False) -> np.ndarray:
        t = np.mod(np.asarray(times, dtype=np.float64), RangePulse.period)
        start = RangePulse.high if descending else RangePulse.low
        sweep = RangePulse.low - RangePulse.high if descending else RangePulse.high - RangePulse.low
        active = (t >= 0) & (t < RangePulse.duration)
        edge = np.minimum(t, RangePulse.duration - t)
        window = np.ones_like(t)
        fade = edge < 0.0003
        window[fade] = 0.5 - 0.5 * np.cos(np.pi * np.maximum(0.0, edge[fade]) / 0.0003)
        phase = 2.0 * math.pi * (start * t + sweep * t * t / (2.0 * RangePulse.duration))
        return np.where(active, np.sin(phase) * window, 0.0)


@dataclass
class RangeReading:
    profile: list[float]
    cm: float | None
    quality: float
    status: str
    candidate: float | None = None
    calibrating: bool = False
    calibration_remaining: float | None = None


class RangeAnalyzer:
    n = 16384

    def __init__(self, rate: float, descending: bool = False) -> None:
        self.rate = rate
        self.hop = int(rate * RangePulse.period)
        chirp_r = np.zeros(self.n, dtype=np.float64)
        chirp_i = np.zeros(self.n, dtype=np.float64)
        count = int(rate * RangePulse.duration)
        start = RangePulse.high if descending else RangePulse.low
        sweep = RangePulse.low - RangePulse.high if descending else RangePulse.high - RangePulse.low
        for k in range(count):
            t = k / rate
            phase = 2.0 * math.pi * (start * t + sweep * t * t / (2.0 * RangePulse.duration))
            w = RangePulse.window(t)
            chirp_r[k] = math.sin(phase) * w
            chirp_i[k] = -math.cos(phase) * w
        ref = np.fft.fft(chirp_r + 1j * chirp_i)
        self.ref_r = ref.real.copy()
        self.ref_i = ref.imag.copy()
        self.baseline = np.zeros(61, dtype=np.float64)
        self.squares = np.zeros(61, dtype=np.float64)
        self.count = 0
        self.previous: float | None = None
        self.stable = 0

    def analyze(self, input_samples: list[float] | np.ndarray) -> RangeReading:
        samples = np.asarray(input_samples, dtype=np.float64)
        if samples.size != self.n:
            self.stable = 0
            self.previous = None
            return RangeReading([], None, 0.0, "Incomplete audio frame · waiting for the next sample")
        spec = np.fft.fft(samples)
        mixed_r = spec.real * self.ref_r + spec.imag * self.ref_i
        mixed_i = spec.imag * self.ref_r - spec.real * self.ref_i
        correlated = np.fft.ifft(mixed_r + 1j * mixed_i)
        magnitude = np.sqrt(correlated.real ** 2 + correlated.imag ** 2)
        end = self.n - int(self.rate * (RangePulse.duration + 0.006)) - 1
        direct = int(np.argmax(magnitude[:end]))
        peak = float(magnitude[direct])
        noise = float(np.median(magnitude))
        if peak <= max(1e-5, noise * 12):
            self.stable = 0
            self.previous = None
            return RangeReading([], None, 0.0, "No clear direct chirp · check the audio route")
        profile = np.zeros(61, dtype=np.float64)
        for cm in range(61):
            lag = cm * 0.02 / 343.0 * self.rate
            index = direct + int(round(lag))
            if 0 <= index < self.n:
                profile[cm] = magnitude[index] / peak
        warmup = int(math.ceil(3.0 / RangePulse.period))
        if self.count < warmup:
            self.count += 1
            self.baseline += profile
            self.squares += profile * profile
            if self.count == warmup:
                self.baseline /= float(warmup)
                variance = np.maximum(0.0, self.squares / float(warmup) - self.baseline * self.baseline)
                self.squares = np.sqrt(variance)
            remaining = float(warmup - self.count) * RangePulse.period
            return RangeReading(
                profile.tolist(),
                None,
                0.0,
                f"Measuring empty desk · keep hands away ({int(math.ceil(remaining))}s)",
                calibrating=True,
                calibration_remaining=remaining,
            )
        excess = np.maximum(0.0, profile - self.baseline)
        candidate = int(8 + np.argmax(excess[8:56]))
        sorted_floor = np.sort(excess[8:61])
        floor = max(0.003, max(self.squares[candidate] * 3.0, sorted_floor[26] * 2.0))
        ratio = excess[candidate] / floor
        rest = [excess[i] for i in range(8, 61) if abs(i - candidate) > 8]
        second = max(rest) if rest else 0.0
        if ratio <= 4 or second >= excess[candidate] * 0.8:
            self.stable = 0
            self.previous = None
            return RangeReading(
                excess.tolist(),
                None,
                min(1.0, ratio / 12.0),
                "Multiple echoes · hold one palm still" if ratio > 4 else "Waiting for a distinct hand echo",
                candidate=float(candidate) if ratio > 1.5 else None,
            )
        cm = float(candidate)
        if self.previous is not None and abs(self.previous - cm) <= 4:
            self.stable += 1
        else:
            self.stable = 1
        self.previous = cm
        return RangeReading(
            excess.tolist(),
            cm if self.stable >= 3 else None,
            min(1.0, ratio / 12.0),
            "Stable echo · experimental estimate" if self.stable >= 3 else "Checking echo stability…",
            candidate=cm,
        )


@dataclass
class PlanePosition:
    x: float
    height: float

    @staticmethod
    def solve(left: float, right: float, span: float) -> PlanePosition | None:
        if not (math.isfinite(left) and math.isfinite(right) and math.isfinite(span)):
            return None
        if left <= 0 or right <= 0 or span < 10:
            return None
        half = span / 2.0
        l = 2.0 * left + half
        r = 2.0 * right + half
        radius = (l * l + r * r - 2.0 * half * half) / (2.0 * (l + r))
        x = (l * l - 2.0 * l * radius - half * half) / (2.0 * half)
        height_squared = radius * radius - x * x
        if height_squared <= 0 or abs(x) > 40 or height_squared > 3600:
            return None
        return PlanePosition(x, math.sqrt(height_squared))


@dataclass
class RangeFrame:
    time: float
    reading: RangeReading


@dataclass
class EchoFlowDrive:
    strength: float
    direction: float
    bands: list[float] = field(default_factory=list)

    @staticmethod
    def measure(frames: list, now: float) -> EchoFlowDrive:
        if not frames or not math.isfinite(now) or not math.isfinite(frames[-1].time):
            return EchoFlowDrive(0.0, 0.0, [])
        latest = frames[-1]
        freshness = max(0.0, min(1.0, 1.0 - (now - latest.time) / 0.5))
        recent = frames[-5:]
        low = 0.0
        high = 0.0
        weight = 0.0
        bands = [0.0] * 24
        for j, frame in enumerate(recent):
            w = float(j + 1)
            weight += w
            for i, value in enumerate(frame.power):
                if not math.isfinite(value):
                    continue
                energy = max(0.0, value)
                if i < len(frame.power) / 2:
                    low += energy * w
                else:
                    high += energy * w
                bands[min(23, i * 24 // max(1, len(frame.power)))] += energy * w
        total = (low + high) / max(1.0, weight)
        strength = min(1.0, math.log1p(total * 8.0) / 3.0) * freshness
        return EchoFlowDrive(
            strength,
            (high - low) / max(0.00001, high + low),
            [min(1.0, value / max(1.0, weight) * 5.0) * freshness for value in bands],
        )
