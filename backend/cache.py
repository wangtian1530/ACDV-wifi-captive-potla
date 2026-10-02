import threading
import time
from typing import Optional, Any

_cache = {}
_cache_lock = threading.Lock()


def get_cache(key: str, ttl: int) -> Optional[Any]:
    with _cache_lock:
        if key in _cache:
            value, timestamp = _cache[key]
            if time.time() - timestamp < ttl:
                return value
    return None


def set_cache(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (value, time.time())


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def clear_cache_by_prefix(prefix: str) -> None:
    with _cache_lock:
        keys_to_delete = [key for key in _cache if key.startswith(prefix)]
        for key in keys_to_delete:
            del _cache[key]
