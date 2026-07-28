"""Process-local one-time confirmation tokens for destructive goal actions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}


def _default_ttl() -> float:
    raw = os.environ.get("GNS3_CONFIRM_TOKEN_TTL_SECONDS", "600")
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 600.0


def target_hash(target: Any) -> str:
    """Stable hash for action target binding."""
    payload = json.dumps(target, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_token(
    action: str,
    target: Any,
    *,
    ttl_seconds: Optional[float] = None,
) -> Tuple[str, float]:
    """Issue a one-time token bound to action+target. Returns (token, expires_at)."""
    ttl = _default_ttl() if ttl_seconds is None else max(0.0, float(ttl_seconds))
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + ttl
    th = target_hash(target)
    with _lock:
        _purge_expired_unlocked()
        _store[token] = {
            "action": action,
            "target_hash": th,
            "expires_at": expires_at,
            "used": False,
        }
    return token, expires_at


def consume_token(token: Optional[str], action: str, target: Any) -> Dict[str, Any]:
    """Validate and consume a token. Returns {ok: bool, error?: str}."""
    if not token:
        return {"ok": False, "error": "confirmation_token required"}
    th = target_hash(target)
    now = time.time()
    with _lock:
        _purge_expired_unlocked(now=now)
        rec = _store.get(token)
        if rec is None:
            return {"ok": False, "error": "invalid or unknown confirmation_token"}
        if rec.get("used"):
            return {"ok": False, "error": "confirmation_token already used"}
        if float(rec.get("expires_at", 0)) < now:
            _store.pop(token, None)
            return {"ok": False, "error": "confirmation_token expired"}
        if rec.get("action") != action:
            return {"ok": False, "error": "confirmation_token action mismatch"}
        if rec.get("target_hash") != th:
            return {"ok": False, "error": "confirmation_token target mismatch"}
        rec["used"] = True
        return {"ok": True}


def reset_tokens_for_tests() -> None:
    """Clear in-memory token store (unit tests only)."""
    with _lock:
        _store.clear()


def _purge_expired_unlocked(*, now: Optional[float] = None) -> None:
    ts = time.time() if now is None else now
    dead = [k for k, v in _store.items() if float(v.get("expires_at", 0)) < ts]
    for k in dead:
        _store.pop(k, None)
