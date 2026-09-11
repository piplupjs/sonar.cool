from __future__ import annotations

import math
import sys

from .analyzer import Analyzer, Reading
from .gestures import DoublePushDetector, ImmediateWave, ScrollMotion, ZoomMotion, zoom_keys
from .ranging import EchoFlowDrive, PlanePosition, RangeAnalyzer, RangePulse
from .session import SignalFrame


def test_check(condition: bool, message: str = "Expectation failed") -> None:
    if not condition:
        sys.stderr.write(f"FAIL {message}\n")
        raise SystemExit(1)


def test_echo_flow() -> None:
    empty = EchoFlowDrive.measure([], now=10)
    test_check(empty.strength == 0, "No samples must produce no reaction")

    def frame(power: list[float]) -> SignalFrame:
        return SignalFrame(10, power, [], [], 96000, 19400, 12, -20)

    toward = frame([0, 0, 0.1, 0.2])
    away = frame([0.2, 0.1, 0, 0])
    test_check(EchoFlowDrive.measure([toward], now=10).direction > 0, "Approaching echo direction")
    test_check(EchoFlowDrive.measure([away], now=10).direction < 0, "Receding echo direction")
    test_check(EchoFlowDrive.measure([toward], now=11).strength == 0, "Stale audio must fade")
    test_check(EchoFlowDrive.measure([frame([0, 0, 0, 0])], now=10).strength == 0, "Silence must not drive animation")
    print("PASS echo artwork input, direction, silence and stale-sample fade")


def test_position() -> None:
    for x in (-8.0, 0.0, 8.0):
        for h in (10.0, 20.0, 30.0):
            d = math.sqrt(x * x + h * h)
            half = 12.0
            left = (math.sqrt((x + half) ** 2 + h * h) + d - half) / 2
            right = (math.sqrt((x - half) ** 2 + h * h) + d - half) / 2
            point = PlanePosition.solve(left, right, 24)
            test_check(point is not None and abs(point.x - x) < 0.001 and abs(point.height - h) < 0.001, "Two-source planar geometry")
    test_check(PlanePosition.solve(55, 8, 24) is None, "Impossible geometry must not draw a point")
    test_check(PlanePosition.solve(float("nan"), 20, 24) is None, "Nonfinite geometry must be rejected")
    for rate in (48000.0, 96000.0):
        left_a = RangeAnalyzer(rate)
        right_a = RangeAnalyzer(rate, descending=True)
        left = right = None
        for frame in range(63):
            samples = []
            for k in range(left_a.n):
                t = (k + frame * left_a.hop) / rate - 0.012
                direct = RangePulse.sample(t) + 0.8 * RangePulse.sample(t - RangePulse.period / 2, descending=True)
                echoes = 0.0
                if frame >= 55:
                    echoes = 0.2 * RangePulse.sample(t - 15 * 0.02 / 343) + 0.16 * RangePulse.sample(
                        t - RangePulse.period / 2 - 25 * 0.02 / 343, descending=True
                    )
                samples.append(direct + echoes)
            left = left_a.analyze(samples)
            right = right_a.analyze(samples)
        sys.stderr.write(f"Stereo test {rate}: L {left.cm} R {right.cm}\n")
        test_check(left.cm is not None and abs(left.cm - 15) <= 4 and right.cm is not None and abs(right.cm - 25) <= 4, "Separate rising/falling chirp echoes")
    print("PASS two-speaker chirp separation and conditional planar geometry")


def test_distance() -> None:
    for rate in (48000.0, 96000.0):
        analyzer = RangeAnalyzer(rate)

        def samples(cm, frame):
            out = []
            for k in range(analyzer.n):
                t = (k + frame * analyzer.hop) / rate - 0.012
                direct = RangePulse.sample(t)
                echo = 0.15 * RangePulse.sample(t - cm * 0.02 / 343) if cm is not None else 0.0
                out.append(direct + echo)
            return out

        test_check(analyzer.analyze([]).cm is None, "Incomplete audio must not crash or report distance")
        reading = analyzer.analyze([0.0] * analyzer.n)
        test_check(reading.cm is None, "Silence must not produce distance")
        for frame in range(55):
            reading = analyzer.analyze(samples(None, frame))
        test_check(reading.cm is None, "Static baseline must not produce distance")
        for cm in (10.0, 20.0, 30.0):
            for frame in range(55, 62):
                reading = analyzer.analyze(samples(cm, frame))
            sys.stderr.write(f"Range test: {rate} Hz, expected {cm}, got {reading.cm}, {reading.status}\n")
            test_check(reading.cm is not None and abs(reading.cm - cm) <= 4, f"Synthetic echo {cm} cm at {rate}")
        reading = analyzer.analyze(samples(None, 63))
        test_check(reading.cm is None, "Lost echo must clear displayed range")
        print(f"PASS matched chirps at {rate}")


def test_zoom_motion() -> None:
    test_check(zoom_keys(0, False, 1) == ["minus"], "Native final return must undo its last step")
    test_check(len(zoom_keys(0, False, 3)) == 3, "Stopping native zoom must undo remaining steps")
    test_check(zoom_keys(0, True, 1) == ["0"], "Browsers retain reset-to-100 shortcut")
    test_check(zoom_keys(0, False, 0) == [], "No native zoom means no reset keystrokes")

    def sample(direction, offset):
        spectrum = [-100.0] * 121
        spectrum[60 + (offset if direction == "APPROACHING" else -offset)] = -30.0
        return Reading(spectrum, [-100.0] * 121, direction, -20, 40, 0.004, opposed_strength=0.0004, bin_width=10)

    def trial(offset, reversed_mapping=False):
        detector = ZoomMotion()
        actions = []
        reset_time = 0.0
        for i in range(100):
            push = i < 7
            direction = "APPROACHING" if push != reversed_mapping else "MOVING AWAY"
            event = detector.feed(sample(direction, offset), now=i * 0.02, reversed_mapping=reversed_mapping)
            if event is not None:
                actions.append(event)
                if event == 0:
                    reset_time = i * 0.02
        test_check(actions == [3, -1, -1, 0], f"Zoom should step back once per level: {actions}")
        return reset_time

    slow = trial(4)
    fast = trial(30)
    test_check(fast < slow and fast < 0.32, "Fast immediate pull must reset without the old cooldown")
    trial(30, reversed_mapping=True)
    detector = ZoomMotion()
    for i in range(20):
        reading = sample("APPROACHING", 30)
        reading.snr = 0
        test_check(detector.feed(reading, now=i * 0.02, reversed_mapping=False) is None, "Weak signal triggered zoom")
    print("PASS speed-paced zoom return")


def test_demo_modes() -> None:
    from .gestures import DemoGestureDetector

    for mode, direction, frames, expected in (("gallery", "APPROACHING", 10, "next"), ("gallery", "MOVING AWAY", 10, "previous")):
        detector = DemoGestureDetector()
        events = []
        t = 0.0

        def feed(d, count):
            nonlocal t
            for _ in range(count):
                event = detector.feed(Reading([], [], d, 0, 40, 0.004), mode, t)
                if event:
                    events.append(event)
                t += 0.02

        feed("Still", 40)
        feed(direction, frames)
        feed("Still", 8)
        feed("MOVING AWAY" if direction == "APPROACHING" else "APPROACHING", 20)
        test_check(events == [expected], f"Demo gesture / return suppression: {mode} {events}")
    print("PASS gallery gesture timing")


def test_wave_calibration() -> None:
    for sign in (1.0, -1.0):
        wave = ImmediateWave()
        events = []
        for frame in range(80):
            motion = 20 <= frame < 48
            first = frame < 33
            value = sign if first else -sign
            bands = [0.0] * 8
            if motion:
                bands[5 if value > 0 else 2] = 2
            event = wave.feed(Reading([], [], "Mixed movement", 0, 40, 0.004 if motion else 0, wave_bands=bands), now=frame * 0.02)
            if event:
                events.append(event)
        test_check(events == ["next" if sign > 0 else "previous"], "Immediate wave / return suppression failed")
    wave = ImmediateWave()
    for frame in range(100):
        reading = Reading([], [], "Mixed movement", 0, 40, 0.004 if frame > 20 else 0, wave_bands=[1] * 8)
        test_check(wave.feed(reading, now=frame * 0.02) is None, "Symmetric motion guessed a direction")
    for sign in (1.0, -1.0):
        protected = ImmediateWave()
        fired = []
        times = []
        for frame in range(120):
            t = frame * 0.02
            forward = 0.3 <= t < 0.44 or 1.6 <= t < 1.74
            returning = 0.70 <= t < 0.90
            bands = [0.0] * 8
            if forward:
                bands[5 if sign > 0 else 2] = 2
            if returning:
                bands[2 if sign > 0 else 5] = 2
            event = protected.feed(Reading([], [], "Mixed movement", 0, 40, 0.004 if forward or returning else 0, wave_bands=bands), now=t)
            if event:
                fired.append(event)
                times.append(t)
        expected = "next" if sign > 0 else "previous"
        test_check(fired == [expected, expected], "Paused return stroke triggered gallery navigation")
        test_check(len(times) == 2 and times[0] <= 0.38, "Protected gallery lost its faster initial response")
    print("PASS immediate waves without training")


def test_analyzer_and_scroll() -> None:
    for rate in (48000.0, 96000.0):
        for offset, expected in ((180.0, "APPROACHING"), (-180.0, "MOVING AWAY"), (0.0, "Still / no clear motion")):
            analyzer = Analyzer(rate, 20000)
            start_motion = int(math.ceil(2.5 * rate / analyzer.hop)) + 4
            result = None
            for frame in range(start_motion + 12):
                samples = []
                for i in range(analyzer.n):
                    t = (frame * analyzer.hop + i) / rate
                    value = 0.05 * math.sin(2 * math.pi * 20000 * t)
                    if frame > start_motion and offset != 0:
                        value += 0.004 * math.sin(2 * math.pi * (20000 + offset) * t)
                    samples.append(value)
                result = analyzer.analyze(samples)
            test_check(result.direction == expected, f"Expected {expected}, got {result.direction}")
            print(f"PASS synthetic {expected} at {rate}")
    detector = DoublePushDetector()
    t = 0.0
    detected = 0

    def feed_push(direction, frames):
        nonlocal t, detected
        for _ in range(frames):
            if detector.feed(direction, t):
                detected += 1
            t += 0.02

    feed_push("APPROACHING", 8)
    feed_push("MOVING AWAY", 6)
    test_check(detected == 0)
    feed_push("APPROACHING", 8)
    feed_push("MOVING AWAY", 6)
    test_check(detected == 1)
    feed_push("APPROACHING", 8)
    feed_push("MOVING AWAY", 6)
    test_check(detected == 1, "Triple push toggled twice")
    feed_push("Still", 60)
    feed_push("APPROACHING", 8)
    feed_push("Still", 60)
    test_check(detected == 1, "Single push toggled")
    feed_push("APPROACHING", 30)
    feed_push("MOVING AWAY", 6)
    feed_push("APPROACHING", 30)
    feed_push("MOVING AWAY", 6)
    test_check(detected == 1, "Long return strokes toggled")
    feed_push("Still", 60)
    feed_push("APPROACHING", 8)
    feed_push("Mixed movement", 6)
    feed_push("APPROACHING", 8)
    feed_push("Mixed movement", 6)
    test_check(detected == 2, "Neutral reversal prevented detection")
    print("PASS passive double push")
    for rate in (48000.0, 96000.0):
        analyzer = Analyzer(rate, 20000)
        detector = DoublePushDetector()
        switches = 0
        for frame in range(int(4.5 * rate / analyzer.hop)):
            samples = []
            for i in range(analyzer.n):
                t = (frame * analyzer.hop + i) / rate
                pulse = (3.1 <= t < 3.32) or (3.5 <= t < 3.72)
                value = 0.05 * math.sin(2 * math.pi * 20000 * t)
                if pulse:
                    value += 0.004 * math.sin(2 * math.pi * 20180 * t)
                samples.append(value)
            reading = analyzer.analyze(samples)
            if detector.feed(reading.direction, now=frame * analyzer.hop / rate):
                switches += 1
        test_check(switches == 1, f"FFT double push failed at {rate}")
        print(f"PASS FFT-to-gesture double push at {rate}")
    continuous = ScrollMotion()
    t = 0.0
    for _ in range(30):
        continuous.feed("MOVING AWAY", 0.004, t)
        test_check(continuous.step(0.02, t) > 0)
        t += 0.02
    before_gap = continuous.velocity
    for _ in range(3):
        continuous.feed("Mixed movement", 0, t)
        continuous.step(0.02, t)
        t += 0.02
    test_check(continuous.velocity >= before_gap * 0.95, "Brief FFT gap caused stutter")
    for _ in range(30):
        continuous.feed("APPROACHING", 0.004, t)
        test_check(continuous.step(0.02, t) >= 0)
        t += 0.02
    continuous.feed("MOVING AWAY", 0.004, t)
    test_check(continuous.step(0.02, t) > 0, "Return blocked next lift")
    continuous.switch_direction(t)
    continuous.feed("MOVING AWAY", 0.004, t)
    test_check(continuous.step(0.02, t) < 0)
    for _ in range(50):
        t += 0.02
        continuous.step(0.02, t)
    test_check(continuous.velocity == 0)
    print("PASS continuous scrolling")


def run_self_test() -> None:
    test_echo_flow()
    test_position()
    test_distance()
    test_zoom_motion()
    test_demo_modes()
    test_wave_calibration()
    test_analyzer_and_scroll()
    print("All Windows Sonar self-tests passed.")

