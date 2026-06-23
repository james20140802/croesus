from __future__ import annotations
import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        value = factory()
        with self._lock:
            self._store[key] = (time.monotonic(), value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._store.clear()
