"""Rate limits, counted in Redis with fixed windows.

Each limit has a name, a key (a user id, an address or an email) and a window. A request
over the limit gets ``429 Too Many Requests`` with ``Retry-After``. If Redis cannot be
reached the request is allowed: an outage of the counter store should not take sign-in
or the API down with it. Limits are configured in ``ymer.config`` (``YMER_RATE_LIMIT_*``)
and can be switched off with ``YMER_RATE_LIMIT_ENABLED=false``.
"""

import hashlib
import logging
import time

from flask import current_app, g, request
from werkzeug.exceptions import TooManyRequests

from ymer.app import settings_for
from ymer.extensions import get_redis

log = logging.getLogger(__name__)
PREFIX = "ymer:rl"


def client_ip() -> str:
    """The caller's address (behind ``YMER_TRUSTED_PROXIES`` proxies, the forwarded one)."""
    return request.remote_addr or "unknown"


def _key(name: str, key: str, window: int) -> tuple[str, int]:
    now = time.time()
    bucket = int(now // window)
    # Hash the key so emails and addresses are not stored in Redis in the clear.
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    retry_after = int((bucket + 1) * window - now) + 1
    return f"{PREFIX}:{name}:{digest}:{bucket}", retry_after


def _enabled() -> bool:
    return settings_for(current_app).rate_limit_enabled


def _too_many(message: str, retry_after: int) -> TooManyRequests:
    g.rate_limited = True
    return TooManyRequests(message, retry_after=retry_after)


def hit(name: str, key: str, limit: int, window: int, message: str | None = None) -> None:
    """Count one request; raise 429 once more than ``limit`` happened in this window."""
    if not _enabled() or limit <= 0:
        return
    redis_key, retry_after = _key(name, key, window)
    try:
        redis = get_redis()
        count = redis.incr(redis_key)
        if count == 1:
            redis.expire(redis_key, window + 5)
    except Exception:  # noqa: BLE001 - fail open
        log.warning("Rate limit %s skipped: Redis unavailable.", name)
        return
    if count > limit:
        raise _too_many(message or "Too many requests. Try again shortly.", retry_after)


def check(name: str, key: str, limit: int, window: int, message: str | None = None) -> None:
    """Raise 429 if ``limit`` has already been reached, without counting this request.

    Pair with :func:`record` to count only some outcomes (e.g. failed sign-ins)."""
    if not _enabled() or limit <= 0:
        return
    redis_key, retry_after = _key(name, key, window)
    try:
        count = int(get_redis().get(redis_key) or 0)
    except Exception:  # noqa: BLE001 - fail open
        log.warning("Rate limit %s skipped: Redis unavailable.", name)
        return
    if count >= limit:
        raise _too_many(message or "Too many requests. Try again shortly.", retry_after)


def record(name: str, key: str, window: int) -> None:
    if not _enabled():
        return
    redis_key, _ = _key(name, key, window)
    try:
        redis = get_redis()
        if redis.incr(redis_key) == 1:
            redis.expire(redis_key, window + 5)
    except Exception:  # noqa: BLE001
        log.warning("Rate limit %s not recorded: Redis unavailable.", name)
