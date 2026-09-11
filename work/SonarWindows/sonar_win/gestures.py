from __future__ import annotations

import math
from dataclasses import dataclass

from .analyzer import Reading


class DoublePushDetector:
    def __init__(self) -> None:
        self.pulse_start: float | None = None
        self.last_toward = -math.inf
        self.previous_pulse: float | None = None
        self.last_sample = -math.inf
        self.cooldown_until = 0.0
        self.feedback = ""
        self.feedback_until = 0.0

    def say(self, message: str, now: float) -> None:
        self.feedback = message
        self.feedback_until = now + 1.2

    def feed(self, direction: str, now: float) -> bool:
        if now - self.last_sample > 0.2:
            self.pulse_start = None
            self.previous_pulse = None
        self.last_sample = now
        if now < self.cooldown_until:
            return False
        if self.previous_pulse is not None and now - self.previous_pulse > 0.85:
            self.previous_pulse = None
            self.say("Second push not detected", now)
        if direction == "APPROACHING":
            if self.pulse_start is None:
                self.pulse_start = now
            self.last_toward = now
            return False
        gap = 0.035 if direction == "MOVING AWAY" else 0.085
        if self.pulse_start is None or now - self.last_toward < gap:
            return False
        start = self.pulse_start
        self.pulse_start = None
        duration = self.last_toward - start + 0.021
        if not (0.04 <= duration <= 0.38):
            self.previous_pulse = None
            self.say("Push too long for a tap" if duration > 0.38 else "Push too brief", now)
            return False
        if self.previous_pulse is not None and 0.13 <= start - self.previous_pulse <= 0.7:
            self.previous_pulse = None
            self.cooldown_until = now + 0.65
            self.say("Direction switched", now)
            return True
        self.previous_pulse = start
        self.say("1 push · push again", now)
        return False


class ScrollMotion:
    def __init__(self) -> None:
        self.velocity = 0.0
        self.target = 0.0
        self.forward = True
        self.toggles = 0
        self.last_away = -math.inf

    def switch_direction(self, now: float) -> None:
        self.forward = not self.forward
        self.toggles += 1
        self.velocity = 0.0
        self.target = 0.0
        self.last_away = -math.inf

    def feed(self, direction: str, strength: float, now: float) -> None:
        if direction == "MOVING AWAY":
            self.last_away = now
            self.target = (1 if self.forward else -1) * min(
                700.0, 110.0 + 120.0 * math.log1p(float(strength) / 0.0003)
            )
        elif (
            direction == "APPROACHING"
            or "Calibrating" in direction
            or "Tone not clear" in direction
        ):
            self.target = 0.0
            self.last_away = -math.inf

    def step(self, dt: float, now: float) -> float:
        if now - self.last_away > 0.09:
            self.target = 0.0
        tau = 0.075 if self.target == 0 else 0.07
        self.velocity += (self.target - self.velocity) * (1.0 - math.exp(-dt / tau))
        if abs(self.velocity) < 2 and self.target == 0:
            self.velocity = 0.0
        return self.velocity * dt


class ImmediateWave:
    def __init__(self) -> None:
        self.quiet_since: float | None = None
        self.armed = False
        self.started: float | None = None
        self.last_sample = -math.inf
        self.cooldown_until = 0.0
        self.balance_sum = 0.0
        self.votes = 0

    def feed(self, r: Reading, now: float) -> str | None:
        if now - self.last_sample > 0.2:
            self.__init__()
            self.last_sample = now
        self.last_sample = now
        if "Calibrating" in r.direction or r.snr <= 15:
            self.armed = False
            self.quiet_since = None
            self.started = None
            self.votes = 0
            self.balance_sum = 0.0
            return None
        if now < self.cooldown_until:
            return None
        moving = r.strength > 0.00022
        if not moving:
            self.started = None
            self.votes = 0
            self.balance_sum = 0.0
            if self.quiet_since is None:
                self.quiet_since = now
            if now - self.quiet_since >= 0.22:
                self.armed = True
                self.started = None
                self.votes = 0
                self.balance_sum = 0.0
            return None
        self.quiet_since = None
        if not self.armed:
            return None
        if self.started is None:
            self.started = now
        if len(r.wave_bands) == 8:
            power = [math.expm1(min(20.0, max(0.0, value))) for value in r.wave_bands]
            away = sum(power[:4])
            toward = sum(power[4:])
            balance = (toward - away) / max(1e-9, toward + away)
        else:
            balance = 1.0 if r.direction == "APPROACHING" else -1.0 if r.direction == "MOVING AWAY" else 0.0
        if abs(balance) > 0.16:
            if self.votes > 0 and balance * self.balance_sum < 0:
                self.started = now
                self.votes = 0
                self.balance_sum = 0.0
            self.balance_sum += balance
            self.votes += 1
        else:
            self.started = now
            self.votes = 0
            self.balance_sum = 0.0
        elapsed = now - (self.started if self.started is not None else now)
        if elapsed > 0.65:
            self.armed = False
            self.started = None
            self.votes = 0
            self.balance_sum = 0.0
            return None
        if elapsed < 0.04 or self.votes < 3 or abs(self.balance_sum) / float(self.votes) <= 0.24:
            return None
        event = "next" if self.balance_sum > 0 else "previous"
        self.armed = False
        self.started = None
        self.votes = 0
        self.balance_sum = 0.0
        self.cooldown_until = now + 0.65
        return event


class WaveCalibration:
    def __init__(self, reversed_mapping: bool = False) -> None:
        self.enabled = True
        self.live = False
        self.message = "Ready · sweep across the keyboard"
        self.reversed = reversed_mapping
        self.detector = ImmediateWave()

    def reset_input(self) -> None:
        self.detector = ImmediateWave()

    def cancel(self) -> None:
        self.reset_input()

    def clear(self) -> None:
        self.reset_input()

    def consume(self, r: Reading, now: float) -> str | None:
        if not self.enabled:
            return None
        if "Calibrating" in r.direction:
            self.message = "Settling audio · hold still briefly"
        elif self.message.startswith("Settling"):
            self.message = "Ready · sweep across the keyboard"
        event = self.detector.feed(r, now)
        if event is None:
            return None
        if self.reversed:
            event = "previous" if event == "next" else "next"
        self.message = "→ Next image" if event == "next" else "← Previous image"
        return event


class DemoGestureDetector:
    def __init__(self) -> None:
        self.direction = ""
        self.began = 0.0
        self.last_motion = 0.0
        self.last_sample = -math.inf
        self.quiet_since: float | None = None
        self.armed = False
        self.last_action = -math.inf

    def feed(self, r: Reading, mode: str, now: float) -> str | None:
        if now - self.last_sample > 0.2:
            self.direction = ""
            self.armed = False
            self.quiet_since = None
        self.last_sample = now
        if "Calibrating" in r.direction or r.snr <= 15:
            self.direction = ""
            self.armed = False
            self.quiet_since = None
            return None
        opposed = r.opposed_strength > 0.0003
        d = "BOTH" if opposed else r.direction
        moving = d in ("APPROACHING", "MOVING AWAY", "BOTH")
        if not moving:
            if self.quiet_since is None:
                self.quiet_since = now
            if now - self.quiet_since >= 0.22 and now - self.last_action > 0.55:
                self.armed = True
        else:
            self.quiet_since = None
        if not self.armed:
            self.direction = ""
            return None
        if self.direction == "" and moving:
            self.direction = d
            self.began = now
            self.last_motion = now
        if d == self.direction:
            self.last_motion = now
        if now - self.last_motion > 0.16 and self.direction == "BOTH":
            self.direction = ""
            return None
        length = self.last_motion - self.began
        event = None
        if self.direction and d != self.direction and now - self.last_motion >= 0.065:
            if 0.055 <= length <= 0.65 and mode == "gallery":
                if self.direction == "APPROACHING":
                    event = "next"
                elif self.direction == "MOVING AWAY":
                    event = "previous"
            self.direction = ""
        if event is not None:
            self.armed = False
            self.direction = ""
            self.last_action = now
            self.quiet_since = None
        return event


class ZoomMotion:
    def __init__(self) -> None:
        self.steps = 0
        self.direction = ""
        self.began = 0.0
        self.last = -math.inf
        self.budget = 0.0

    @property
    def enlarged(self) -> bool:
        return self.steps > 0

    def clear_evidence(self) -> None:
        self.direction = ""
        self.last = -math.inf
        self.budget = 0.0

    @staticmethod
    def return_rate(r: Reading) -> float:
        if len(r.spectrum) != len(r.baseline) or len(r.spectrum) <= 7 or r.bin_width <= 0:
            return 15.0
        center = len(r.spectrum) // 2
        weighted = 0.0
        total = 0.0
        for i, value in enumerate(r.spectrum):
            if abs(i - center) < 3:
                continue
            if (r.direction == "MOVING AWAY") != (i < center):
                continue
            energy = max(0.0, 10.0 ** (value / 10.0) - 2.0 * 10.0 ** (r.baseline[i] / 10.0))
            weighted += energy * abs(i - center) * r.bin_width
            total += energy
        if total <= 0 or not math.isfinite(weighted):
            return 15.0
        return min(45.0, max(5.0, weighted / total * 0.15))

    def feed(self, r: Reading, now: float, reversed_mapping: bool) -> int | None:
        gap = now - self.last
        result = None
        if gap > 0.16:
            self.direction = ""
            self.budget = 0.0
        if not (r.snr > 15 and r.strength > 0.0003 and r.direction in ("APPROACHING", "MOVING AWAY")):
            if now - self.began > 0.16:
                self.direction = ""
                self.budget = 0.0
        else:
            toward = (r.direction == "APPROACHING") != reversed_mapping
            if self.direction != r.direction:
                self.direction = r.direction
                self.began = now
                self.budget = 0.0
            if toward:
                if self.steps == 0 and now - self.began >= 0.10:
                    self.steps = 3
                    self.direction = ""
                    result = 3
            elif self.steps > 0:
                finite_gap = gap if math.isfinite(gap) else 0.0
                self.budget += min(0.06, max(0.0, finite_gap)) * self.return_rate(r)
                if now - self.began >= 0.025 and self.budget >= 1:
                    self.budget -= 1
                    self.steps -= 1
                    result = 0 if self.steps == 0 else -1
        self.last = now
        return result


ZOOM_SCALES = [1.0, 1.15, 1.3, 1.5]


def zoom_keys(action: int, browser: bool, remaining: int) -> list[str]:
    if action > 0:
        return ["equal"] * 3
    if action < 0:
        return ["minus"]
    if browser:
        return ["0"]
    return ["minus"] * max(0, remaining)


@dataclass
class SystemScrollState:
    remainder: float = 0.0

    def reset(self) -> None:
        self.remainder = 0.0

    def pixels(self, points: float) -> int:
        self.remainder += points
        value = int(self.remainder) if self.remainder >= 0 else -int(-self.remainder)
        self.remainder -= value
        return value

