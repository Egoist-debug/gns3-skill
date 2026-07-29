"""One-time confirmation tokens for destructive goal actions.

Tokens are bound to ``action`` + ``target`` (sha256), single-use, and TTL-limited
(``GNS3_CONFIRM_TOKEN_TTL_SECONDS``, default 600s, min 30s). The store is
**file-backed** so a token issued in one CLI process (the preview call) can be
consumed in a later CLI process (the execute call) — the documented cross-call
flow in ``references/playbooks.md``. The file lives under ``XDG_RUNTIME_DIR``
(or ``~/.cache``, with a ``tempfile.gettempdir()`` last resort) at
``gns3-skill/confirm-tokens.json``, mode 0600. If the store file is ever
unreadable/writable, the module degrades transparently to the previous
in-process dict so destructive goals can never error due to disk trouble.

Override for tests / custom deployments:
``GNS3_CONFIRM_TOKEN_STORE`` — absolute path to the store file.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
# In-process fallback store (only used when disk backing is unavailable).
_store: Dict[str, Dict[str, Any]] = {}
_disk_disabled: bool = False


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


def _store_path() -> Path:
    """Resolve the on-disk token store path.

    Order: ``GNS3_CONFIRM_TOKEN_STORE`` > ``XDG_RUNTIME_DIR`` > ``~/.cache`` >
    ``tempfile.gettempdir()`` (last resort). The parent dir is created 0700;
    the file itself is given 0600 on every write.
    """
    override = os.environ.get("GNS3_CONFIRM_TOKEN_STORE")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).expanduser().is_dir():
        root = Path(base).expanduser()
    else:
        cache = Path("~/.cache").expanduser()
        root = cache if cache.exists() and cache.is_dir() else Path(tempfile.gettempdir())
    return root / "gns3-skill" / "confirm-tokens.json"


def _load_unlocked() -> Dict[str, Dict[str, Any]]:
    """Load the persistent store, or the in-memory fallback if disk is off.

    Returns a *fresh* dict the caller may mutate freely. Stale/expired records
    are pruned on load.
    """
    if _disk_disabled:
        return dict(_store)
    path = _store_path()
    try:
        if not path.is_file():
            return {}
        # 0600 file — skip world/group-readable stores rather than failing open.
        try:
            st = path.stat()
        except OSError:
            return {}
        if st.st_mode & 0o077 and st.st_uid == os.getuid():
            # Too permissive and we own it — drop and start clean.
            try:
                path.unlink()
            except OSError:
                pass
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        now = time.time()
        clean: Dict[str, Dict[str, Any]] = {}
        for tok, rec in data.items():
            if not isinstance(rec, dict):
                continue
            exp = float(rec.get("expires_at", 0) or 0)
            if exp < now:
                continue
            clean[tok] = rec
        return clean
    except (OSError, ValueError, TypeError):
        # Disk unavailable — fall back to in-memory for this process only.
        return dict(_store)


def _save_unlocked(store: Dict[str, Dict[str, Any]]) -> None:
    """Persist the store atomically with mode 0600, or keep it in memory."""
    if _disk_disabled:
        _store.clear()
        _store.update(store)
        return
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Best-effort 0700 on the directory.
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        payload = json.dumps(store, separators=(",", ":"))
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
        except OSError:
            # fdopen owns the fd; if it failed the fd is already closed by os.fdopen.
            pass
    except OSError:
        # Disk unavailable — keep the store in memory so the API stays usable.
        _store.clear()
        _store.update(store)


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
        store = _load_unlocked()
        # Prune expired now (also happens on save/load, but keep it tight).
        now = time.time()
        for k in [k for k, v in store.items() if float(v.get("expires_at", 0)) < now]:
            store.pop(k, None)
        store[token] = {
            "action": action,
            "target_hash": th,
            "expires_at": expires_at,
            "used": False,
        }
        _save_unlocked(store)
    return token, expires_at


def consume_token(token: Optional[str], action: str, target: Any) -> Dict[str, Any]:
    """Validate and consume a token. Returns {ok: bool, error?: str}."""
    if not token:
        return {"ok": False, "error": "confirmation_token required"}
    th = target_hash(target)
    now = time.time()
    with _lock:
        store = _load_unlocked()
        rec = store.get(token)
        if rec is None:
            return {"ok": False, "error": "invalid or unknown confirmation_token"}
        if rec.get("used"):
            return {"ok": False, "error": "confirmation_token already used"}
        if float(rec.get("expires_at", 0)) < now:
            store.pop(token, None)
            _save_unlocked(store)
            return {"ok": False, "error": "confirmation_token expired"}
        if rec.get("action") != action:
            return {"ok": False, "error": "confirmation_token action mismatch"}
        if rec.get("target_hash") != th:
            return {"ok": False, "error": "confirmation_token target mismatch"}
        rec["used"] = True
        _save_unlocked(store)
        return {"ok": True}


def reset_tokens_for_tests() -> None:
    """Clear persisted and in-memory tokens (unit tests / explicit reset only)."""
    with _lock:
        _store.clear()
        if not _disk_disabled:
            path = _store_path()
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass


def _purge_expired_unlocked(*, now: Optional[float] = None) -> None:
    """Legacy no-op kept for API stability; expiry is pruned at load/save time."""
    return
