from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

from ..manifest import TransformSpec


class TransformError(ValueError):
    """Raised when a resolved target transform is unknown or invalid."""


_PARAMETERS: dict[str, set[str]] = {
    "date_to_epoch_seconds": {"declared_format"},
    "dense_vector_to_tensor": set(),
    "ensure_array": set(),
    "lowercase_string": set(),
}


def validate_transform(spec: TransformSpec) -> None:
    allowed = _PARAMETERS.get(spec.operation)
    if allowed is None:
        raise TransformError(f"Unknown target transform: {spec.operation}")
    unexpected = sorted(set(spec.parameters) - allowed)
    if unexpected:
        raise TransformError(
            f"Transform {spec.operation} has unsupported parameters: {', '.join(unexpected)}"
        )
    if spec.operation == "date_to_epoch_seconds" and not isinstance(
        spec.parameters.get("declared_format"), str
    ):
        raise TransformError("date_to_epoch_seconds requires a declared_format string")


def apply_transforms(value: Any, transforms: tuple[TransformSpec, ...]) -> Any:
    current = value
    for spec in transforms:
        validate_transform(spec)
        current = _apply_transform(current, spec)
    return current


def _apply_transform(value: Any, spec: TransformSpec) -> Any:
    if spec.operation == "ensure_array":
        return value if isinstance(value, list) else [value]
    if spec.operation == "dense_vector_to_tensor":
        if not isinstance(value, list) or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
        ):
            raise TransformError("Vector sample must be an array of numbers")
        return {"values": value}
    if spec.operation == "date_to_epoch_seconds":
        declared_format = str(spec.parameters["declared_format"])
        return _map_values(value, lambda item: _date_to_epoch_seconds(item, declared_format))
    if spec.operation == "lowercase_string":
        return _map_values(value, lambda item: item.lower() if isinstance(item, str) else item)
    raise TransformError(f"Unknown target transform: {spec.operation}")


def _map_values(value: Any, transform: Callable[[Any], Any]) -> Any:
    if isinstance(value, list):
        return [transform(item) for item in value]
    return transform(value)


def _date_to_epoch_seconds(value: Any, declared_format: str) -> int:
    if isinstance(value, bool):
        raise TransformError("Boolean is not a valid date")
    formats = {item.strip() for item in declared_format.split("||") if item.strip()}
    epoch_unit = (
        "epoch_second" if "epoch_second" in formats
        else "epoch_millis" if "epoch_millis" in formats
        else None
    )
    numeric: float | None = None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            numeric = None
    if numeric is not None:
        if epoch_unit == "epoch_second":
            return math.floor(numeric)
        if epoch_unit == "epoch_millis":
            return math.floor(numeric / 1000)
        raise TransformError(f"Numeric date has no declared epoch unit: {declared_format}")
    if not isinstance(value, str):
        raise TransformError(f"Unsupported date value: {value!r}")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TransformError(f"Unsupported ISO-8601 date value: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return math.floor(parsed.timestamp())
