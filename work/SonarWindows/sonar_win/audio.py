from __future__ import annotations

import math
import threading
from collections.abc import Callable

import numpy as np

from .ranging import RangePulse

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None


def builtin_device_name(name: str) -> bool:
    lowered = name.lower()
    excluded = ("bluetooth", "airpods", "headset", "hands-free", "a2dp", "sco", "wireless")
    return not any(token in lowered for token in excluded)


def pick_devices() -> tuple[int | None, int | None, str]:
    if sd is None:
        raise RuntimeError("sounddevice is required for live audio on Windows.")
    hostapis = sd.query_hostapis()
    wasapi = next((i for i, api in enumerate(hostapis) if "wasapi" in api["name"].lower()), None)
    devices = sd.query_devices()
    input_id = None
    output_id = None
    input_name = ""
    output_name = ""
    preferred = ("realtek", "idt", "conexant", "high definition", "built-in", "internal")
    ranked: list[tuple[int, int, dict]] = []
    for index, device in enumerate(devices):
        if wasapi is not None and device["hostapi"] != wasapi:
            continue
        name = str(device["name"])
        if not builtin_device_name(name):
            continue
        rank = 0 if any(token in name.lower() for token in preferred) else 1
        ranked.append((rank, index, device))
    ranked.sort(key=lambda item: (item[0], item[1]))
    for _rank, index, device in ranked:
        name = str(device["name"])
        if device["max_input_channels"] > 0 and input_id is None:
            input_id = index
            input_name = name
        if device["max_output_channels"] > 0 and output_id is None:
            output_id = index
            output_name = name
    if input_id is None or output_id is None:
        default_in, default_out = sd.default.device
        input_id = input_id if input_id is not None else default_in
        output_id = output_id if output_id is not None else default_out
        if input_id is not None:
            input_name = str(devices[input_id]["name"])
        if output_id is not None:
            output_name = str(devices[output_id]["name"])
    if input_id is None or output_id is None:
        raise RuntimeError("Built-in audio device not found.")
    return input_id, output_id, f"{output_name} + {input_name} • Bluetooth excluded"


class HardwareAudio:
    def __init__(
        self,
        tone: float,
        amplitude: float,
        receive: Callable[[np.ndarray], None],
        ranging: bool = False,
        positioning: bool = False,
        sample_rate: float | None = None,
    ) -> None:
        self.tone = tone
        self.amplitude = amplitude
        self.receive = receive
        self.ranging = ranging
        self.positioning = positioning
        self.phase = 0.0
        self.elapsed = 0.0
        self.input_rate = sample_rate or 48000.0
        self.output_rate = sample_rate or 48000.0
        self._lock = threading.Lock()
        self._streams: list = []

    def start(self) -> None:
        if sd is None:
            raise RuntimeError("Install sounddevice to use live microphone and speakers.")
        input_id, output_id, _route = pick_devices()
        in_info = sd.query_devices(input_id)
        out_info = sd.query_devices(output_id)
        self.input_rate = float(in_info["default_samplerate"] or 48000.0)
        self.output_rate = float(out_info["default_samplerate"] or 48000.0)
        needed = (RangePulse.high if self.ranging else self.tone) * 2 + 1000
        if min(self.input_rate, self.output_rate) <= needed:
            raise RuntimeError("Built-in audio sample rate is too low for this tone.")

        def input_callback(indata, frames, time, status) -> None:  # noqa: ARG001
            self.receive(np.asarray(indata[:, 0], dtype=np.float32).copy())

        def output_callback(outdata, frames, time, status) -> None:  # noqa: ARG001
            channels = outdata.shape[1]
            with self._lock:
                times = self.elapsed + np.arange(frames, dtype=np.float64) / self.output_rate
                envelope = np.minimum(1.0, times / 0.15)
                if self.ranging:
                    left = RangePulse.samples(times)
                else:
                    phases = self.phase + 2.0 * math.pi * self.tone * np.arange(frames, dtype=np.float64) / self.output_rate
                    left = np.sin(phases)
                    self.phase = float((phases[-1] + 2.0 * math.pi * self.tone / self.output_rate) % (2.0 * math.pi))
                left = left * self.amplitude * envelope
                outdata[:, 0] = left
                if channels > 1:
                    if self.positioning:
                        right = RangePulse.samples(times - RangePulse.period / 2.0, descending=True)
                        outdata[:, 1] = right * self.amplitude * envelope
                    elif self.ranging:
                        outdata[:, 1] = 0.0
                    else:
                        outdata[:, 1] = left
                    if channels > 2:
                        outdata[:, 2:] = 0.0
                self.elapsed += frames / self.output_rate

        input_stream = sd.InputStream(
            device=input_id,
            channels=1,
            samplerate=self.input_rate,
            dtype="float32",
            callback=input_callback,
            blocksize=1024,
        )
        out_channels = 2 if int(out_info["max_output_channels"] or 0) >= 2 else 1
        output_stream = sd.OutputStream(
            device=output_id,
            channels=out_channels,
            samplerate=self.output_rate,
            dtype="float32",
            callback=output_callback,
            blocksize=1024,
        )
        input_stream.start()
        output_stream.start()
        self._streams = [input_stream, output_stream]

    def stop(self) -> None:
        for stream in self._streams:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._streams = []
