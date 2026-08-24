from __future__ import annotations

import math
import re
from typing import Any, Mapping

from ..manifest import TargetFieldPlan


_ARRAY = re.compile(r"^array<(.+)>$")
_DENSE_TENSOR = re.compile(
    r"^tensor<float>\([A-Za-z_][A-Za-z0-9_]*\[(\d+)]\)$"
)


class FeedValidationError(ValueError):
    """Raised when a transformed source value cannot be fed to its target field."""


def validate_document_values(
    document_number: int,
    fields: Mapping[str, Any],
    plans: Mapping[str, TargetFieldPlan],
) -> None:
    """Validate values derived from untrusted source documents against target types."""
    for name, plan in plans.items():
        if name not in fields:
            continue
        problem = _target_value_problem(fields[name], plan.type)
        if problem:
            raise FeedValidationError(f"document {document_number} field {name}: {problem}")


def _target_value_problem(value: Any, vespa_type: str) -> str | None:
    array_match = _ARRAY.fullmatch(vespa_type)
    if array_match:
        if not isinstance(value, list):
            return f"expected {vespa_type}, found {type(value).__name__}"
        for index, item in enumerate(value):
            problem = _target_value_problem(item, array_match.group(1))
            if problem:
                return f"item {index}: {problem}"
        return None

    tensor_match = _DENSE_TENSOR.fullmatch(vespa_type)
    if tensor_match:
        values = value.get("values") if isinstance(value, dict) else None
        expected_size = int(tensor_match.group(1))
        if not isinstance(values, list):
            return f"expected {vespa_type} tensor values"
        if len(values) != expected_size:
            return f"expected {expected_size} tensor values, found {len(values)}"
        if not all(_is_finite_number(item) for item in values):
            return "tensor values must be finite numbers"
        return None

    if vespa_type == "string":
        return None if isinstance(value, str) else f"expected string, found {type(value).__name__}"
    if vespa_type == "bool":
        return None if isinstance(value, bool) else f"expected bool, found {type(value).__name__}"
    if vespa_type in {"int", "long"}:
        if not isinstance(value, int) or isinstance(value, bool):
            return f"expected {vespa_type}, found {type(value).__name__}"
        if vespa_type == "int" and not -(2**31) <= value <= 2**31 - 1:
            return "value is outside the Vespa int range"
        if vespa_type == "long" and not -(2**63) <= value <= 2**63 - 1:
            return "value is outside the Vespa long range"
        return None
    if vespa_type in {"float", "double"}:
        return None if _is_finite_number(value) else f"expected finite {vespa_type}"
    return f"target type {vespa_type} has no feed validator"


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
