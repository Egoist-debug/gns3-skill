"""Registry and invocation contracts for the GNS3 skill."""
from __future__ import annotations

import inspect
import json
import types
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Literal, Mapping, Optional, Union, get_args, get_origin, get_type_hints, is_typeddict


class OperationTier(str, Enum):
    GOAL = "goal"
    EXPERT = "expert"


class OperationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    CONFIRMATION_REQUIRED = "confirmation_required"
    ERROR = "error"


OperationCallable = Callable[..., Awaitable[Any]]
_MISSING = object()


@dataclass(frozen=True)
class OperationParameter:
    name: str
    annotation: Any
    required: bool
    default: Any = _MISSING
    sensitive: bool = False

    def to_schema(self) -> Dict[str, Any]:
        schema = _annotation_schema(self.annotation)
        if self.default is not _MISSING:
            schema["default"] = self.default
        if self.sensitive:
            schema["sensitive"] = True
        return schema


@dataclass(frozen=True)
class OperationSpec:
    """Complete registry record for one operation."""

    identifier: str
    tier: OperationTier
    callable: OperationCallable
    summary: str
    sensitive_parameters: frozenset[str] = field(default_factory=frozenset)

    def parameters(self) -> Mapping[str, OperationParameter]:
        signature = inspect.signature(self.callable)
        try:
            hints = get_type_hints(self.callable)
        except Exception:
            hints = {}

        parameters: Dict[str, OperationParameter] = {}
        for name, parameter in signature.parameters.items():
            if name == "context":
                continue
            if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                raise ValueError(f"operation {self.identifier!r} may not use variadic parameters")
            annotation = hints.get(name, parameter.annotation)
            required = parameter.default is inspect.Parameter.empty
            parameters[name] = OperationParameter(
                name=name,
                annotation=annotation,
                required=required,
                default=_MISSING if required else parameter.default,
                sensitive=name in self.sensitive_parameters,
            )

        for name, annotation, default in (
            ("server_url", Optional[str], "http://localhost:3080"),
            ("username", Optional[str], None),
            ("password", Optional[str], None),
        ):
            if name not in parameters:
                parameters[name] = OperationParameter(
                    name=name,
                    annotation=annotation,
                    required=False,
                    default=default,
                    sensitive=name in self.sensitive_parameters,
                )
        return parameters

    def parameter_schema(self) -> Dict[str, Any]:
        parameters = self.parameters()
        return {
            "type": "object",
            "properties": {name: value.to_schema() for name, value in parameters.items()},
            "required": [name for name, value in parameters.items() if value.required],
            "additionalProperties": False,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identifier": self.identifier,
            "tier": self.tier.value,
            "summary": self.summary,
            "schema": self.parameter_schema(),
        }


@dataclass
class OperationError(Exception):
    """Expected structured failure at the runtime seam."""

    error_type: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    @property
    def type(self) -> str:
        return self.error_type

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"type": self.error_type, "message": self.message}
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(frozen=True)
class OperationOutcome:
    """Normalized result for one registered invocation."""

    status: OperationStatus
    result: Optional[Mapping[str, Any]] = None
    error: Optional[OperationError] = None

    def __post_init__(self) -> None:
        if self.status is OperationStatus.ERROR:
            if self.error is None or self.result is not None:
                raise ValueError("error outcomes require error and forbid result")
        elif self.result is None or self.error is not None:
            raise ValueError("non-error outcomes require result and forbid error")

    @classmethod
    def success(cls, result: Optional[Mapping[str, Any]] = None) -> "OperationOutcome":
        return cls(OperationStatus.SUCCESS, result=dict(result or {}))

    @classmethod
    def failure(cls, error: OperationError) -> "OperationOutcome":
        return cls(OperationStatus.ERROR, error=error)

    def to_dict(self, operation: str, tier: Union[OperationTier, str]) -> Dict[str, Any]:
        tier_value = tier.value if isinstance(tier, OperationTier) else str(tier)
        payload: Dict[str, Any] = {
            "status": self.status.value,
            "operation": operation,
            "tier": tier_value,
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = dict(self.result or {})
        return payload


def _annotation_schema(annotation: Any) -> Dict[str, Any]:
    if annotation in (inspect.Parameter.empty, Any):
        return {}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        nullable = len(non_none) != len(args)
        if len(non_none) == 1:
            schema = _annotation_schema(non_none[0])
            if nullable:
                schema["nullable"] = True
            return schema
        schema = {"anyOf": [_annotation_schema(arg) for arg in non_none]}
        if nullable:
            schema["nullable"] = True
        return schema
    if origin is Literal:
        schema: Dict[str, Any] = {"enum": list(args)}
        literal_types = {type(value) for value in args}
        if literal_types == {str}:
            schema["type"] = "string"
        elif literal_types == {bool}:
            schema["type"] = "boolean"
        elif literal_types == {int}:
            schema["type"] = "integer"
        elif literal_types <= {int, float}:
            schema["type"] = "number"
        return schema
    if is_typeddict(annotation):
        hints = get_type_hints(annotation)
        required_keys = getattr(annotation, "__required_keys__", frozenset())
        return {
            "type": "object",
            "properties": {
                name: _annotation_schema(value) for name, value in hints.items()
            },
            "required": [name for name in hints if name in required_keys],
            "additionalProperties": False,
        }
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if origin is list:
        return {"type": "array", "items": _annotation_schema(args[0] if args else Any)}
    if origin in (dict, Mapping):
        return {
            "type": "object",
            "additionalProperties": _annotation_schema(args[1] if len(args) > 1 else Any),
        }
    return {}


def assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"registry metadata is not JSON serializable: {exc}") from exc
