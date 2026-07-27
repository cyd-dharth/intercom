import time

from app.errors import RateLimitedError

_buckets: dict[str, tuple[float, float]] = {}

_ROUTE_LIMITS = {
    "auth": (5, 60),
    "widget_session": (20, 60),
    "message_send": (60, 60),
    "kb_search": (30, 60),
}


def check_rate_limit(route_class: str, key: str) -> None:
    limit, window = _ROUTE_LIMITS[route_class]
    bucket_key = f"{route_class}:{key}"
    now = time.monotonic()
    tokens, last = _buckets.get(bucket_key, (float(limit), now))
    elapsed = now - last
    refill_rate = limit / window
    tokens = min(float(limit), tokens + elapsed * refill_rate)
    if tokens < 1:
        _buckets[bucket_key] = (tokens, now)
        raise RateLimitedError("Too many requests, please slow down", code="rate_limited")
    tokens -= 1
    _buckets[bucket_key] = (tokens, now)
