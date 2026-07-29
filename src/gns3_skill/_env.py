"""Shared environment-variable parsing helpers."""

from __future__ import annotations

import os


def env_float(name: str, default: float) -> float:
    """Read a float from env; return *default* when unset or invalid."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    """Read an int from env; return *default* when unset or invalid."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default
