"""Shared goal-tool status envelopes and step records."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_PARTIAL = "partial"
STATUS_CONFIRMATION_REQUIRED = "confirmation_required"
STATUS_CONFLICT = "conflict"

STEP_SUCCESS = "success"
STEP_SKIPPED = "skipped"
STEP_CHANGED = "changed"
STEP_FAILED = "failed"


def step_entry(
    step: str,
    status: str,
    *,
    detail: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    mutated: bool = False,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"step": step, "status": status}
    if detail:
        entry["detail"] = detail
    if error:
        entry["error"] = error
    if mutated:
        entry["mutated"] = True
    return entry


def goal_envelope(
    goal: str,
    status: str,
    steps: List[Dict[str, Any]],
    *,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    next_hint: Optional[Any] = None,
    **extra: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "goal": goal,
        "steps": steps,
        "result": result if result is not None else {},
        "error": error,
        "next": next_hint,
    }
    payload.update(extra)
    return payload


def error_envelope(
    goal: str,
    error: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    next_hint: Optional[Any] = None,
    **extra: Any,
) -> Dict[str, Any]:
    return goal_envelope(
        goal,
        STATUS_ERROR,
        steps or [],
        error=error,
        next_hint=next_hint,
        **extra,
    )


def conflict_envelope(
    goal: str,
    steps: List[Dict[str, Any]],
    *,
    existing: Dict[str, Any],
    expected: Dict[str, Any],
    message: str,
    **extra: Any,
) -> Dict[str, Any]:
    return goal_envelope(
        goal,
        STATUS_CONFLICT,
        steps,
        error=message,
        result={"existing": existing, "expected": expected},
        **extra,
    )


def confirmation_required_envelope(
    goal: str,
    steps: List[Dict[str, Any]],
    *,
    action: str,
    target: Dict[str, Any],
    impact: Any,
    confirmation_token: str,
    expires_at: float,
    **extra: Any,
) -> Dict[str, Any]:
    return goal_envelope(
        goal,
        STATUS_CONFIRMATION_REQUIRED,
        steps,
        result={
            "action": action,
            "target": target,
            "impact": impact,
            "confirmation_token": confirmation_token,
            "expires_at": expires_at,
        },
        next_hint="Re-call with confirmation_token after explicit user approval",
        **extra,
    )
