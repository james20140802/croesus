from __future__ import annotations
import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[Any, threading.Lock] = {}

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        # Fast-path: fresh entry already cached
        with self._lock:
            hit = self._store.get(key)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            # Obtain (or create) per-key lock while still holding global lock
            keylock = self._key_locks.setdefault(key, threading.Lock())

        # Acquire per-key lock; different keys do NOT block each other
        with keylock:
            # Double-check: another thread may have filled the entry
            with self._lock:
                hit = self._store.get(key)
                if hit and time.monotonic() - hit[0] < self._ttl:
                    return hit[1]

            # Call factory OUTSIDE global lock but still inside per-key lock
            value = factory()

            with self._lock:
                self._store[key] = (time.monotonic(), value)

        return value

    def invalidate(self) -> None:
        with self._lock:
            self._store.clear()
            self._key_locks.clear()
