"""JSON-only registry runner for GNS3 skill operations."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from .contracts import OperationError, OperationOutcome
from .registry import describe_operation, get_operation, list_operations
from .runtime import build_inputs, exit_code, invoke, parse_json_object, usage_error


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


def _fail(
    operation: Optional[str],
    message: str,
    details: Optional[Dict[str, object]] = None,
    *,
    tier: Optional[str] = None,
) -> int:
    _emit(usage_error(operation, message, details, tier=tier))
    return 2


def _list_command(arguments: List[str]) -> int:
    tier = "goal"
    seen_tier = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("--tier="):
            if seen_tier:
                return _fail(None, "--tier may be supplied only once")
            tier = argument.split("=", 1)[1]
            seen_tier = True
        elif argument == "--tier":
            if seen_tier or index + 1 >= len(arguments):
                return _fail(None, "--tier requires one value and may be supplied only once")
            index += 1
            tier = arguments[index]
            seen_tier = True
        else:
            return _fail(None, "unexpected list argument", {"argument": argument})
        index += 1
    if tier not in {"goal", "expert", "all"}:
        return _fail(None, "tier must be goal, expert, or all", {"tier": tier})
    specs = list_operations(tier)
    _emit({
        "tier": tier,
        "operations": [
            {"identifier": spec.identifier, "tier": spec.tier.value, "summary": spec.summary}
            for spec in specs
        ],
        "total": len(specs),
    })
    return 0


def _describe_command(arguments: List[str]) -> int:
    if len(arguments) != 1:
        return _fail(arguments[0] if arguments else None, "describe requires exactly one operation")
    identifier = arguments[0]
    metadata = describe_operation(identifier)
    if metadata is None:
        return _fail(identifier, "unknown operation", {"operation": identifier})
    _emit(metadata)
    return 0


def _parse_run_arguments(arguments: List[str]) -> Tuple[Dict[str, str], Optional[str]]:
    kv: Dict[str, str] = {}
    json_raw: Optional[str] = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--json":
            if json_raw is not None:
                raise OperationError("usage", "--json may be supplied only once")
            if index + 1 >= len(arguments):
                raise OperationError("usage", "--json requires a JSON object")
            index += 1
            json_raw = arguments[index]
        elif argument.startswith("--json="):
            if json_raw is not None:
                raise OperationError("usage", "--json may be supplied only once")
            json_raw = argument.split("=", 1)[1]
        elif argument.startswith("--") and "=" in argument:
            key, value = argument[2:].split("=", 1)
            if not key:
                raise OperationError("usage", "parameter name may not be empty")
            if key in kv:
                raise OperationError(
                    "usage", "duplicate --key=value parameter", {"parameter": key}
                )
            kv[key] = value
        elif argument.startswith("--"):
            raise OperationError(
                "usage",
                "operation parameters must use --key=value form",
                {"parameter": argument[2:]},
            )
        else:
            raise OperationError("usage", "unexpected run argument", {"argument": argument})
        index += 1
    return kv, json_raw


def _stdin_json() -> Optional[str]:
    try:
        if sys.stdin.isatty():
            return None
        raw = sys.stdin.read().strip()
    except (OSError, AttributeError):
        return None
    return raw or None


def _run_command(arguments: List[str]) -> int:
    if not arguments:
        return _fail(None, "run requires exactly one operation")
    identifier = arguments[0]
    spec = get_operation(identifier)
    if spec is None:
        return _fail(identifier, "unknown operation", {"operation": identifier})

    try:
        kv, json_raw = _parse_run_arguments(arguments[1:])
        stdin_raw = _stdin_json()
        if json_raw is not None and stdin_raw is not None:
            raise OperationError(
                "usage", "--json and stdin JSON are mutually exclusive",
                {"sources": ["--json", "stdin"]},
            )
        body = None
        if json_raw is not None:
            body = parse_json_object(json_raw, source="--json")
        elif stdin_raw is not None:
            body = parse_json_object(stdin_raw, source="stdin")
        inputs = build_inputs(spec, kv, body)
    except OperationError as exc:
        _emit(usage_error(identifier, exc.message, exc.details, tier=spec.tier.value))
        return 2

    try:
        outcome = asyncio.run(invoke(spec, inputs))
    except KeyboardInterrupt:
        outcome = OperationOutcome.failure(
            OperationError("interrupted", "operation interrupted")
        )
    _emit(outcome.to_dict(spec.identifier, spec.tier))
    return exit_code(outcome)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Dispatch only the approved list, describe, and run commands."""
    arguments = list(argv) if argv is not None else list(sys.argv[1:])
    if not arguments:
        return _fail(None, "command must be list, describe, or run")
    command, rest = arguments[0], arguments[1:]
    if command == "list":
        return _list_command(rest)
    if command == "describe":
        return _describe_command(rest)
    if command == "run":
        return _run_command(rest)
    return _fail(command, "unknown command; expected list, describe, or run", {"command": command})


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
