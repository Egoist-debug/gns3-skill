"""Ordered fail-stop step runner for goal tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .envelopes import (
    STATUS_ERROR,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    STEP_FAILED,
    step_entry,
)

StepFn = Callable[[], Union[Dict[str, Any], Awaitable[Dict[str, Any]]]]


@dataclass
class Step:
    name: str
    fn: StepFn
    # When True, failure stops the runner (default). When False, record and continue.
    stop_on_fail: bool = True


@dataclass
class WorkflowResult:
    status: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    stopped_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "steps": self.steps,
            "error": self.error,
            "stopped_at": self.stopped_at,
        }


async def run_steps(steps: List[Step]) -> WorkflowResult:
    """Execute steps in order; fail-stop by default.

    Each step function returns a step entry dict (or raises).
    Required keys: ``step`` (optional if Step.name used), ``status``.
    Status values: success | skipped | changed | failed.
    """
    recorded: List[Dict[str, Any]] = []

    for step in steps:
        try:
            outcome = step.fn()
            if hasattr(outcome, "__await__"):
                outcome = await outcome  # type: ignore[misc]
            if not isinstance(outcome, dict):
                raise TypeError(f"step {step.name!r} must return a dict")
            entry = dict(outcome)
            entry.setdefault("step", step.name)
            status = entry.get("status")
            if status not in ("success", "skipped", "changed", "failed"):
                raise ValueError(f"invalid step status {status!r} for {step.name}")
        except Exception as exc:
            entry = step_entry(step.name, STEP_FAILED, error=str(exc))
            recorded.append(entry)
            if step.stop_on_fail:
                return WorkflowResult(
                    status=_rollup(recorded, forced_error=True),
                    steps=recorded,
                    error=str(exc),
                    stopped_at=step.name,
                )
            continue

        recorded.append(entry)
        if entry["status"] == STEP_FAILED and step.stop_on_fail:
            return WorkflowResult(
                status=_rollup(recorded, forced_error=True),
                steps=recorded,
                error=entry.get("error") or f"step {step.name} failed",
                stopped_at=step.name,
            )

    return WorkflowResult(status=_rollup(recorded), steps=recorded)


def _rollup(steps: List[Dict[str, Any]], *, forced_error: bool = False) -> str:
    if not steps:
        return STATUS_SUCCESS
    statuses = [s.get("status") for s in steps]
    any_failed = any(s == STEP_FAILED for s in statuses)
    any_changed = any(
        s == "changed" or bool(step.get("mutated"))
        for s, step in zip(statuses, steps)
    )
    if forced_error or any_failed:
        if any_changed:
            return STATUS_PARTIAL
        return STATUS_ERROR
    return STATUS_SUCCESS
