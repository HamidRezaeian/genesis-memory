"""In-memory key-value store with TTL cache."""
import time


class KVStore:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, key):
        self._d.pop(key, None)

    def keys(self):
        return sorted(self._d.keys())


class TTLCache:
    def __init__(self, ttl_seconds=60.0, clock=None):
        self.ttl = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._d = {}

    def get(self, key, default=None):
        if key not in self._d:
            return default
        value, _ = self._d[key]
        return value

    def set(self, key, value):
        self._d[key] = (value, self._clock())

    def expire(self, key):
        """Drop KEY if its TTL has elapsed. Returns True if dropped."""
        if key not in self._d:
            return False
        _, born = self._d[key]
        # BUG B3 (planted): inverted comparison expires FRESH entries.
        if self._clock() - born < self.ttl:
            del self._d[key]
            return True
        return False

    def purge(self):
        dead = [k for k in list(self._d) if self.expire(k)]
        return len(dead)
