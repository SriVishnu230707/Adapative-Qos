"""Explicit clock domains and deterministic deadline checks."""

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Protocol

from adaptive_qos.models import Intent, Record


class Clock(Protocol):
    boot_id: str

    def now_ns(self) -> int: ...


class ClockDomainError(ValueError):
    pass


@dataclass(frozen=True)
class SystemClock:
    boot_id: str

    @classmethod
    def for_linux(cls) -> "SystemClock":
        return cls(Path("/proc/sys/kernel/random/boot_id").read_text().strip())

    def now_ns(self) -> int:
        return time.monotonic_ns()


@dataclass
class FakeClock:
    boot_id: str
    monotonic_ns: int = 0

    def __post_init__(self) -> None:
        if type(self.monotonic_ns) is not int or self.monotonic_ns < 0:
            raise ValueError("fake clock must start at a nonnegative integer")

    def now_ns(self) -> int:
        return self.monotonic_ns

    def advance_ms(self, milliseconds: int) -> None:
        if type(milliseconds) is not int or milliseconds < 0:
            raise ValueError("clock advancement must be a nonnegative integer")
        self.monotonic_ns += milliseconds * 1_000_000


def age_ns(record: Record, clock: Clock) -> int:
    if record.boot_id != clock.boot_id:
        raise ClockDomainError("observation belongs to a different boot/clock domain")
    age = clock.now_ns() - record.observed_mono_ns
    if age < 0:
        raise ClockDomainError("observation is in the future")
    return age


def is_fresh(record: Record, clock: Clock, max_age_ms: int) -> bool:
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise ValueError("max_age_ms must be a positive integer")
    return age_ns(record, clock) < max_age_ms * 1_000_000


def intent_is_active(intent: Intent, clock: Clock, *, owner_epoch: str) -> bool:
    if intent.boot_id != clock.boot_id or intent.owner_epoch != owner_epoch:
        return False
    now = clock.now_ns()
    return (
        intent.state == "active"
        and intent.observed_mono_ns <= now
        and intent.start_mono_ns <= now < intent.expires_mono_ns
    )
