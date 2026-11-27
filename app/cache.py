import time
import threading


class SafeCache:
    """Thread-safe TTL cache ជាមួយ per-key lock ការពារ Thundering Herd និង Stale Fallback"""

    def __init__(self):
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def _get_key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.Lock()
            return self._key_locks[key]

    def get_or_fetch(self, key: str, ttl: float, fetcher):
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry and (now - entry[0]) < ttl:
                return entry[1]

        key_lock = self._get_key_lock(key)
        with key_lock:
            now = time.monotonic()
            with self._lock:
                entry = self._data.get(key)
                if entry and (now - entry[0]) < ttl:
                    return entry[1]
            try:
                value = fetcher()
                if value is not None:
                    with self._lock:
                        self._data[key] = (time.monotonic(), value)
                    return value
            except Exception as e:
                print(f"[CACHE:{key}] fetch error: {e}")
            with self._lock:
                entry = self._data.get(key)
                return entry[1] if entry else None


cache = SafeCache()