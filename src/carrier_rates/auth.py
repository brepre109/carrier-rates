"""OAuth token cache shared by both carriers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Hashable


@dataclass
class _Entry:
    token: str
    expires_at: float


class TokenCache:
    """Caches bearer tokens until shortly before they expire.

    ``skew`` seconds are shaved off the carrier's ``expires_in`` so a token is never
    used in the last moments of its life. ``clock`` is injectable for tests.
    """

    def __init__(self, skew: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.skew = skew
        self.clock = clock
        self._entries: dict[Hashable, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if self.clock() >= entry.expires_at:
                del self._entries[key]
                return None
            return entry.token

    def put(self, key: Hashable, token: str, expires_in: float) -> None:
        lifetime = max(float(expires_in) - self.skew, 0.0)
        with self._lock:
            self._entries[key] = _Entry(token, self.clock() + lifetime)

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)
