"""
CallRecorder -- a thin proxy that logs every method call made against the
object it wraps.

Why this matters: AgentGuard needs to see ALL calls that actually reach a
resource (e.g. the real inbox), not just the ones an agent asked for
through the front door. That gap -- what was authorized vs. what actually
happened to the resource -- is exactly how a rogue/compromised
connector's undeclared side effects get caught.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CallRecord:
    action: str
    args: tuple
    kwargs: dict
    timestamp: float


class CallRecorder:
    """Wrap any object; every method call on it gets logged, then passed through."""

    def __init__(self, target, label: str = ""):
        self._target = target
        self._label = label
        self.log: list[CallRecord] = []

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            self.log.append(CallRecord(name, args, kwargs, time.time()))
            return attr(*args, **kwargs)

        return wrapped

    def calls_since(self, index: int) -> list[CallRecord]:
        return self.log[index:]
