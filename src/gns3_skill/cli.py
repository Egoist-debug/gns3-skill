"""GNS3 skill CLI — thin dispatcher over tool functions.

Usage:
  gns3 list
  gns3 <tool_name> [--arg=value | --json '{...}' | raw JSON on stdin]
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, get_args, get_origin, get_type_hints


def _load_tools() -> Dict[str, Callable[..., Any]]:
    """Discover gns3_* coroutine callables from the tools module."""
    import gns3_skill.server as server

    tools: Dict[str, Callable[..., Any]] = {}
    for name, obj in vars(server).items():
        if not name.startswith("gns3_"):
            continue
        if inspect.iscoroutinefunction(obj):
            tools[name] = obj
        elif callable(obj) and inspect.iscoroutinefunction(inspect.unwrap(obj)):
            tools[name] = inspect.unwrap(obj)
    return dict(sorted(tools.items()))


def _parse_bool(raw: str) -> bool:
    v = raw.strip().lower()
    if v in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected bool, got {raw!r}")


def _coerce(value: str, annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is type(None):
        return None
    non_none = [a for a in args if a is not type(None)] if args else []
    if origin is not None and non_none and type(None) in args:
        if value.strip().lower() in {"null", "none", ""}:
            return None
        return _coerce(value, non_none[0])

    if annotation is inspect.Parameter.empty or annotation is Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    if annotation is bool:
        return _parse_bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is str:
        return value

    if origin in (list, dict):
        return json.loads(value)

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _build_kwargs(
    fn: Callable[..., Any],
    kv: Mapping[str, str],
    json_body: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    kwargs: Dict[str, Any] = {}
    if json_body:
        for k, v in json_body.items():
            if k in sig.parameters:
                kwargs[k] = v

    for k, raw in kv.items():
        if k not in sig.parameters:
            raise SystemExit(f"unknown argument --{k} for this tool")
        ann = hints.get(k, sig.parameters[k].annotation)
        kwargs[k] = _coerce(raw, ann)

    return kwargs


def _read_json_arg(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("--json must be a JSON object")
    return data


def _read_stdin_json() -> Optional[Dict[str, Any]]:
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("stdin JSON must be an object")
    return data


async def _invoke(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Any:
    return await fn(**kwargs)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry. Returns process exit code."""
    parser = argparse.ArgumentParser(
        prog="gns3",
        description="GNS3 skill CLI. Dispatches to gns3_skill tool functions.",
    )
    parser.add_argument(
        "tool",
        nargs="?",
        help="Tool name (e.g. gns3_prepare_lab). Use 'list' to show tools.",
    )
    parser.add_argument(
        "--json",
        dest="json_args",
        default=None,
        help="JSON object of arguments",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON result (default)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact JSON output",
    )
    args, unknown = parser.parse_known_args(list(argv) if argv is not None else None)

    if not args.tool or args.tool in {"list", "help"}:
        tools = _load_tools()
        print("Available tools:")
        for name in tools:
            print(f"  {name}")
        print(f"\nTotal: {len(tools)}")
        print("\nInvoke: gns3 <tool> [--key=value ...] [--json '{...}']")
        return 0

    tool_name = args.tool
    if not tool_name.startswith("gns3_"):
        tool_name = f"gns3_{tool_name}"

    tools = _load_tools()
    if tool_name not in tools:
        print(
            f"unknown tool: {args.tool!r}. Run `gns3 list` for names.",
            file=sys.stderr,
        )
        return 2

    fn = tools[tool_name]

    kv: Dict[str, str] = {}
    for item in unknown:
        if item.startswith("--") and "=" in item:
            key, val = item[2:].split("=", 1)
            kv[key] = val
        elif item.startswith("--"):
            print(f"use --{item[2:]}=value form (got {item!r})", file=sys.stderr)
            return 2
        else:
            print(f"unexpected argument: {item!r}", file=sys.stderr)
            return 2

    try:
        json_body = _read_json_arg(args.json_args)
        stdin_body = _read_stdin_json()
        if json_body and stdin_body:
            print("provide either --json or stdin JSON, not both", file=sys.stderr)
            return 2
        body = json_body or stdin_body
        kwargs = _build_kwargs(fn, kv, body)
    except SystemExit as exc:
        msg = exc.code if isinstance(exc.code, str) else str(exc)
        print(msg, file=sys.stderr)
        return 2
    except Exception as e:
        print(f"argument error: {e}", file=sys.stderr)
        return 2

    try:
        result = asyncio.run(_invoke(fn, kwargs))
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        err = {"status": "error", "tool": tool_name, "error": str(e)}
        print(
            json.dumps(err, indent=None if args.compact else 2, default=str),
            file=sys.stderr,
        )
        return 1

    indent = None if args.compact else 2
    print(json.dumps(result, indent=indent, default=str, ensure_ascii=False))
    if isinstance(result, dict) and result.get("status") in {"error", "failed"}:
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
