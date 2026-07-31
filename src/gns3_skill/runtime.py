"""Deep invocation runtime for registered GNS3 operations."""
from __future__ import annotations

import inspect
import json
import logging
import re
import types
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Union, get_args, get_origin

from .contracts import OperationError, OperationOutcome, OperationSpec, OperationStatus
from .gns3_client import GNS3APIClient, GNS3Config
from .server_lifecycle import ensure_gns3_server, normalize_server_url

logger = logging.getLogger(__name__)
_AUTH_STATUS_RE = re.compile(r"(?:HTTP\s*|error\s*\[)(401|403)\b", re.IGNORECASE)


@dataclass
class OperationContext:
    """One invocation's resolved connection state and lazy REST client."""

    server_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    _config: GNS3Config = field(init=False, repr=False)
    _ensure_result: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False)
    _client: Optional[GNS3APIClient] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        config = GNS3Config.from_env(
            server_url=self.server_url,
            username=self.username,
            password=self.password,
        )
        config.server_url = normalize_server_url(config.server_url)
        self._config = config
        self.server_url = config.server_url
        self.username = config.username
        self.password = config.password

    @property
    def config(self) -> GNS3Config:
        return self._config

    async def ensure(self, *, force: bool = False) -> Dict[str, Any]:
        if self._ensure_result is None or force:
            self._ensure_result = await ensure_gns3_server(
                self._config.server_url,
                username=self._config.username,
                password=self._config.password,
                force=force,
            )
        return self._ensure_result

    async def client(self, *, force_ensure: bool = False) -> GNS3APIClient:
        if self._client is not None and not force_ensure:
            return self._client
        ensured = await self.ensure(force=force_ensure)
        if ensured.get("status") != "success":
            raise _error_from_payload(ensured)
        if self._client is None:
            self._client = GNS3APIClient(self._config)
        return self._client


def parse_json_object(raw: str, *, source: str) -> Dict[str, Any]:
    """Parse one JSON object while rejecting duplicate keys."""

    def pairs(items: list[tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise OperationError(
                    "usage",
                    f"duplicate key {key!r} in {source} JSON",
                    {"parameter": key, "source": source},
                )
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise OperationError(
            "usage",
            f"invalid {source} JSON constant",
            {"source": source, "constant": value},
        )

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except OperationError:
        raise
    except json.JSONDecodeError as exc:
        raise OperationError(
            "usage",
            f"malformed {source} JSON",
            {"source": source, "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(value, dict):
        raise OperationError("usage", f"{source} JSON must be an object", {"source": source})
    return value


def build_inputs(
    spec: OperationSpec,
    kv: Mapping[str, str],
    json_body: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Merge, validate, and coerce one operation's CLI inputs."""

    parameters = spec.parameters()
    body = dict(json_body or {})
    overlap = sorted(set(kv).intersection(body))
    if overlap:
        raise OperationError(
            "usage",
            "parameters may not be supplied by both JSON and --key=value",
            {"parameters": overlap},
        )
    supplied = set(kv).union(body)
    unknown = sorted(supplied.difference(parameters))
    if unknown:
        raise OperationError("usage", "unknown operation parameters", {"parameters": unknown})
    missing = sorted(
        name for name, parameter in parameters.items()
        if parameter.required and name not in supplied
    )
    if missing:
        raise OperationError("usage", "missing required operation parameters", {"parameters": missing})

    values: Dict[str, Any] = {}
    for name, value in body.items():
        if not _matches_annotation(value, parameters[name].annotation):
            raise OperationError(
                "usage",
                f"invalid JSON value type for parameter {name!r}",
                {"parameter": name},
            )
        values[name] = value
    for name, raw in kv.items():
        try:
            values[name] = _coerce_scalar(raw, parameters[name].annotation)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OperationError(
                "usage",
                f"invalid --{name}=value",
                {"parameter": name},
            ) from exc
    return values


async def invoke(spec: OperationSpec, inputs: Mapping[str, Any]) -> OperationOutcome:
    """Invoke one operation behind the sole exception and normalization seam."""

    values = dict(inputs)
    secrets = _sensitive_strings(spec, values)
    try:
        context = OperationContext(
            server_url=values.pop("server_url", None),
            username=values.pop("username", None),
            password=values.pop("password", None),
        )
        raw = await spec.callable(context=context, **values)
        outcome = _redact_outcome(normalize_outcome(raw), secrets)
        _assert_json_outcome(outcome)
        return outcome
    except OperationError as exc:
        return OperationOutcome.failure(_redact_error(exc, secrets))
    except Exception as exc:
        message = _redact_text(str(exc) or exc.__class__.__name__, secrets)
        logger.error("Operation %s failed: %s", spec.identifier, message)
        return OperationOutcome.failure(_error_from_exception_message(message))


def normalize_outcome(raw: Any) -> OperationOutcome:
    if isinstance(raw, OperationOutcome):
        return raw
    if raw is None:
        return OperationOutcome.success()
    if not isinstance(raw, Mapping):
        return OperationOutcome.success({"value": raw})

    payload = dict(raw)
    payload.pop("goal", None)
    raw_status = payload.pop("status", OperationStatus.SUCCESS.value)
    if raw_status == "failed":
        raw_status = OperationStatus.ERROR.value
    try:
        status = OperationStatus(str(raw_status))
    except ValueError as exc:
        raise OperationError("runtime", f"operation returned unsupported status {raw_status!r}") from exc

    raw_error = payload.pop("error", None)
    if status is OperationStatus.ERROR:
        return OperationOutcome.failure(_error_from_payload(payload, raw_error=raw_error))

    nested_result = payload.pop("result", None)
    if isinstance(nested_result, Mapping):
        result: Dict[str, Any] = dict(nested_result)
    elif nested_result is None:
        result = {}
    else:
        result = {"value": nested_result}
    for key, value in payload.items():
        if value is not None and key not in result:
            result[key] = value
    if raw_error and "message" not in result:
        result["message"] = _error_message(raw_error)
    return OperationOutcome(status=status, result=result)


def exit_code(outcome: OperationOutcome) -> int:
    if outcome.status in (OperationStatus.SUCCESS, OperationStatus.CONFIRMATION_REQUIRED):
        return 0
    return 1


def usage_error(
    operation: Optional[str],
    message: str,
    details: Optional[Mapping[str, Any]] = None,
    *,
    tier: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "status": OperationStatus.ERROR.value,
        "operation": operation,
        "tier": tier,
        "error": OperationError("usage", message, details or {}).to_dict(),
    }


def _error_from_payload(payload: Mapping[str, Any], *, raw_error: Any = None) -> OperationError:
    error_value = payload.get("error") if raw_error is None else raw_error
    details: Dict[str, Any] = {}
    error_type = "operation"
    if isinstance(error_value, Mapping):
        error_type = str(error_value.get("type") or error_value.get("error_type") or error_type)
        message = str(error_value.get("message") or error_value.get("error") or "operation failed")
        nested_details = error_value.get("details")
        if isinstance(nested_details, Mapping):
            details.update(nested_details)
    else:
        message = str(error_value or "operation failed")
    for key, value in payload.items():
        if key not in {"status", "error"} and value is not None:
            details.setdefault(key, value)
    http_status = details.get("http_status")
    match = _AUTH_STATUS_RE.search(message)
    if http_status in (401, 403) or match:
        error_type = "auth"
        if http_status not in (401, 403) and match:
            details["http_status"] = int(match.group(1))
    return OperationError(error_type, message, details)




def _error_message(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("message") or value.get("error") or "operation failed")
    return str(value)

def _sensitive_strings(
    spec: OperationSpec, inputs: Mapping[str, Any]
) -> tuple[str, ...]:
    parameters = spec.parameters()
    values = {
        value
        for name, value in inputs.items()
        if name in parameters
        and parameters[name].sensitive
        and isinstance(value, str)
        and value
    }
    return tuple(sorted(values, key=len, reverse=True))


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        value = value.replace(secret, "<redacted>")
    return value


def _redact_value(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redact_error(
    error: OperationError, secrets: tuple[str, ...]
) -> OperationError:
    return OperationError(
        error.error_type,
        _redact_text(error.message, secrets),
        _redact_value(error.details, secrets),
    )


def _redact_outcome(
    outcome: OperationOutcome, secrets: tuple[str, ...]
) -> OperationOutcome:
    if outcome.error is not None:
        return OperationOutcome.failure(_redact_error(outcome.error, secrets))
    return OperationOutcome(
        status=outcome.status,
        result=_redact_value(outcome.result or {}, secrets),
    )


def _assert_json_outcome(outcome: OperationOutcome) -> None:
    value: Any = outcome.error.to_dict() if outcome.error is not None else outcome.result
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OperationError(
            "runtime", "operation returned data that is not valid JSON"
        ) from exc


def _error_from_exception_message(message: str) -> OperationError:
    match = _AUTH_STATUS_RE.search(message)
    if match:
        return OperationError("auth", message, {"http_status": int(match.group(1))})
    return OperationError("runtime", message)


def _coerce_scalar(raw: str, annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) != len(args) and raw.strip().lower() in {"", "null", "none"}:
            return None
        last_error: Optional[Exception] = None
        for candidate in non_none:
            try:
                return _coerce_scalar(raw, candidate)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        if last_error:
            raise last_error
    if annotation in (inspect.Parameter.empty, Any):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if annotation is str:
        return raw
    if annotation is bool:
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise ValueError("expected boolean")
    if annotation is int:
        return int(raw)
    if annotation is float:
        return float(raw)
    if origin in (list, dict):
        value = json.loads(raw)
        if not _matches_annotation(value, annotation):
            raise TypeError("JSON value does not match parameter type")
        return value
    value = json.loads(raw)
    if not _matches_annotation(value, annotation):
        raise TypeError("JSON value does not match parameter type")
    return value


def _matches_annotation(value: Any, annotation: Any) -> bool:
    if annotation in (inspect.Parameter.empty, Any):
        return True
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        return any(_matches_annotation(value, arg) for arg in args)
    if annotation is type(None):
        return value is None
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    if origin is list:
        item_type = args[0] if args else Any
        return isinstance(value, list) and all(_matches_annotation(item, item_type) for item in value)
    if origin in (dict, Mapping):
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return isinstance(value, dict) and all(
            _matches_annotation(key, key_type) and _matches_annotation(item, value_type)
            for key, item in value.items()
        )
    return True
