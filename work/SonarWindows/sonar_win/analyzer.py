from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def hann_norm(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float64)
    window = 0.5 * (1.0 - np.cos(2.0 * np.pi * k / n))
    scale = np.sqrt(n / np.sum(window * window))
    return (window * scale).astype(np.float64)


@dataclass
class Reading:
    spectrum: list[float]
    baseline: list[float]
    direction: str
    carrier_db: float
    snr: float
    strength: float
    wave_bands: list[float] = field(default_factory=list)
    opposed_strength: float = 0.0
    waveform: list[float] = field(default_factory=list)
    sample_rate: float = 0.0
    first_frequency: float = 0.0
    bin_width: float = 0.0
    calibration_remaining: float | None = None


class Analyzer:
    n = 8192
    hop = 2048

    def __init__(self, rate: float, tone: float) -> None:
        self.rate = rate
        self.tone = tone
        self.window = hann_norm(self.n)
        self.baseline: np.ndarray | None = None
        self.frames = 0
        self.history: list[str] = []

    def analyze(self, input_samples: list[float] | np.ndarray) -> Reading:
        samples = np.asarray(input_samples, dtype=np.float64)
        if samples.size < self.n:
            samples = np.pad(samples, (0, self.n - samples.size))
        elif samples.size > self.n:
            samples = samples[: self.n]
        real = samples * self.window
        spec = np.fft.fft(real, n=self.n)
        out_r = spec.real
        out_i = spec.imag
        nyquist = self.n // 2
        center = int(round(self.tone / self.rate * self.n))
        radius = max(8, int(600 / self.rate * self.n))
        center = min(max(radius + 5, center), nyquist - radius - 5)
        bins = np.arange(center - radius, center + radius + 1)
        denom = float(self.n * self.n)
        power = np.maximum(1e-16, (out_r[bins] * out_r[bins] + out_i[bins] * out_i[bins]) / denom)
        db = 10.0 * np.log10(power)
        carrier = float(np.max(db[radius - 1 : radius + 2]))
        floor = float((np.sum(db[:5]) + np.sum(db[-5:])) / 10.0)
        if self.baseline is None:
            self.baseline = power.copy()
        calibrating = (self.frames * self.hop) / self.rate < 2.5
        if calibrating:
            a = 1.0 / float(self.frames + 1)
            self.baseline = self.baseline * (1.0 - a) + power * a
        self.frames += 1
        left = 0.0
        right = 0.0
        for i, p in enumerate(power):
            if abs(i - radius) < 3:
                continue
            excess = max(0.0, float(p - self.baseline[i] * 2.0))
            if i < radius:
                left += excess
            else:
                right += excess
        reference = max(float(np.sum(power[radius - 1 : radius + 2])), 1e-12)
        strength = max(left, right) / reference
        raw = "Still / no clear motion"
        if strength > 0.0003 and carrier - floor > 15:
            if right > left * 1.7:
                raw = "APPROACHING"
            elif left > right * 1.7:
                raw = "MOVING AWAY"
            else:
                raw = "Mixed movement"
        self.history.append(raw)
        if len(self.history) > 2:
            self.history.pop(0)
        stable = len(self.history) == 2 and all(item == raw for item in self.history)
        if calibrating:
            direction = "Calibrating — hands still"
        elif carrier - floor < 15:
            direction = "Tone not clear — try another frequency"
        elif stable:
            direction = raw
        else:
            direction = "Listening…"
        bands = [0.0] * 8
        count = len(power)
        for i, p in enumerate(power):
            if abs(i - radius) < 3:
                continue
            band = min(7, i * 8 // count)
            bands[band] += max(0.0, float(p - self.baseline[i] * 2.0) / reference)
        bands = [float(np.log1p(value / 0.0003)) for value in bands]
        remaining = max(0.0, 2.5 - float((self.frames - 1) * self.hop) / self.rate) if calibrating else None
        waveform = samples[-512:].astype(np.float32).tolist()
        return Reading(
            spectrum=db.astype(np.float32).tolist(),
            baseline=(10.0 * np.log10(np.maximum(self.baseline, 1e-16))).astype(np.float32).tolist(),
            direction=direction,
            carrier_db=float(np.float32(carrier)),
            snr=float(np.float32(carrier - floor)),
            strength=float(np.float32(strength)),
            wave_bands=bands,
            opposed_strength=float(np.float32(min(left, right) / reference)),
            waveform=waveform,
            sample_rate=self.rate,
            first_frequency=float(center - radius) * self.rate / float(self.n),
            bin_width=self.rate / float(self.n),
            calibration_remaining=remaining,
        )
