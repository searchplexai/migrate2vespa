"""Resolve normalized migration requirements into Vespa field plans.

This module is deliberately source-independent. Source implementations describe field
meaning, observed shape, and required capabilities; this planner owns every
Vespa-specific type and indexing choice.
"""

from __future__ import annotations

import re

from ..manifest import (
    AnnPlan,
    CardinalityState,
    FieldAssessment,
    LogicalType,
    MatchPlan,
    RequiredCapability,
    TargetFieldPlan,
    TransformSpec,
)


_SCALAR_TYPES = {
    LogicalType.TEXT: "string",
    LogicalType.STRING: "string",
    LogicalType.BOOLEAN: "bool",
    LogicalType.INTEGER: "int",
    LogicalType.LONG: "long",
    LogicalType.FLOAT: "float",
    LogicalType.DOUBLE: "double",
    LogicalType.TEMPORAL: "long",
}

_ATTRIBUTE_CAPABILITIES = {
    RequiredCapability.EXACT_MATCH,
    RequiredCapability.FILTER,
    RequiredCapability.RANGE,
    RequiredCapability.SORT,
    RequiredCapability.GROUP,
}


def _sanitize_field_name(value: str) -> str:
    """Return a conservative Vespa identifier for a source field path."""
    cleaned = re.sub(
        r"[^a-zA-Z0-9_]",
        "_",
        value.replace(".", "__").replace("-", "_"),
    ).strip("_")
    if not cleaned:
        cleaned = "field"
    if cleaned[0].isdigit():
        cleaned = f"field_{cleaned}"
    return cleaned


def validate_field_plan(
    field: FieldAssessment,
    plan: TargetFieldPlan,
) -> tuple[str, ...]:
    """Validate a persisted target plan against normalized field requirements."""
    issues = list(_capability_issues(field, plan))
    expected_type = _target_type(field)
    if expected_type is None:
        issues.append("Normalized field facts do not establish a target type")
    elif plan.type != expected_type:
        issues.append(
            f"Target type {plan.type} conflicts with normalized type {expected_type}"
        )
    expected_source_path = field.source_value_path or field.source_name
    if plan.source_path != expected_source_path:
        issues.append(
            f"Target source_path {plan.source_path} conflicts with normalized source path {expected_source_path}"
        )
    if field.logical_type is LogicalType.VECTOR:
        if plan.ann is None:
            issues.append("Vector target plan has no ANN metadata")
        elif plan.ann.distance_metric != field.distance_metric:
            issues.append(
                "Target ANN distance metric conflicts with normalized vector semantics"
            )
    return tuple(dict.fromkeys(issues))


def _build_plan(field: FieldAssessment) -> TargetFieldPlan | None:
    """Build a candidate from normalized facts before compatibility validation."""
    if field.logical_type is None:
        return None

    capabilities = set(field.required_capabilities)
    target_type = _target_type(field)
    if target_type is None:
        return None

    indexing = _indexing(field.logical_type, capabilities)
    if not indexing:
        return None

    match = _match_plan(field, capabilities, indexing)
    rank = (
        "filter"
        if field.logical_type is LogicalType.STRING
        and RequiredCapability.FILTER in capabilities
        and "attribute" in indexing
        else None
    )
    transforms = list(field.value_transforms)
    if field.logical_type is LogicalType.VECTOR:
        transforms.append(TransformSpec("dense_vector_to_tensor"))
    if target_type.startswith("array<"):
        transforms.append(TransformSpec("ensure_array"))

    ann = None
    if field.logical_type is LogicalType.VECTOR:
        if field.distance_metric is None:
            return None
        ann = AnnPlan(
            distance_metric=field.distance_metric,
            hnsw_enabled=RequiredCapability.ANN in capabilities,
        )

    return TargetFieldPlan(
        name=_sanitize_field_name(field.source_name),
        source_path=field.source_value_path or field.source_name,
        type=target_type,
        indexing=tuple(indexing),
        match=match,
        rank=rank,
        bm25=RequiredCapability.TEXT_MATCH in capabilities,
        ann=ann,
        transforms=tuple(_unique_transforms(transforms)),
        generation_note=field.generation_note,
    )


def apply_plan(field: FieldAssessment) -> tuple[str, ...]:
    """Resolve the canonical target plan and return blocking issues."""
    plan, issues = _resolve_plan(field)
    if issues:
        plan = None
    field.target_plan = plan
    return issues


def _resolve_plan(
    field: FieldAssessment,
) -> tuple[TargetFieldPlan | None, tuple[str, ...]]:
    plan = _build_plan(field)
    if plan is None:
        return None, ("Normalized field facts are insufficient for a safe Vespa target plan",)
    issues = _capability_issues(field, plan)
    return (None, issues) if issues else (plan, ())


def _capability_issues(
    field: FieldAssessment,
    plan: TargetFieldPlan,
) -> tuple[str, ...]:
    """Reject capability/type combinations that do not preserve target semantics."""
    logical_type = field.logical_type
    capabilities = set(field.required_capabilities)
    available = set(plan.indexing)
    comparable_types = {
        LogicalType.INTEGER,
        LogicalType.LONG,
        LogicalType.FLOAT,
        LogicalType.DOUBLE,
        LogicalType.TEMPORAL,
    }
    exact_types = comparable_types | {LogicalType.STRING, LogicalType.BOOLEAN}
    grouping_types = exact_types
    checks = {
        RequiredCapability.TEXT_MATCH: (
            logical_type is LogicalType.TEXT
            and plan.match is not None
            and plan.match.mode == "text"
            and "index" in available,
            "an indexed text representation",
        ),
        RequiredCapability.EXACT_MATCH: (
            logical_type in exact_types and "attribute" in available,
            "an exact-compatible attribute representation",
        ),
        RequiredCapability.FILTER: (
            logical_type in exact_types and "attribute" in available,
            "a filter-compatible attribute representation",
        ),
        RequiredCapability.RANGE: (
            logical_type in comparable_types and "attribute" in available,
            "an ordered numeric or temporal attribute representation",
        ),
        RequiredCapability.SORT: (
            logical_type in grouping_types and "attribute" in available,
            "a sortable scalar attribute representation",
        ),
        RequiredCapability.GROUP: (
            logical_type in grouping_types and "attribute" in available,
            "a grouping-compatible scalar attribute representation",
        ),
        RequiredCapability.ANN: (
            logical_type is LogicalType.VECTOR
            and plan.type.startswith("tensor<")
            and {"attribute", "index"}.issubset(available),
            "an indexed tensor attribute",
        ),
    }
    return tuple(
        f"Required capability {capability.value} needs {checks[capability][1]}; "
        "the normalized field facts do not provide equivalent Vespa semantics"
        for capability in field.required_capabilities
        if not checks[capability][0]
    )


def _target_type(field: FieldAssessment) -> str | None:
    if field.logical_type is LogicalType.VECTOR:
        if not field.vector_dimensions or not field.vector_cell_type:
            return None
        scalar = f"tensor<{field.vector_cell_type}>(x[{field.vector_dimensions}])"
    else:
        scalar = _SCALAR_TYPES.get(field.logical_type)
        if scalar is None:
            return None

    multivalue = field.cardinality_observation.state in {
        CardinalityState.MULTI_OBSERVED,
        CardinalityState.MIXED_OBSERVED,
    }
    if multivalue and field.logical_type is not LogicalType.VECTOR:
        return f"array<{scalar}>"
    return scalar


def _indexing(
    logical_type: LogicalType,
    capabilities: set[RequiredCapability],
) -> list[str]:
    if logical_type is LogicalType.VECTOR:
        return ["attribute", "index"] if RequiredCapability.ANN in capabilities else ["attribute"]
    result = ["summary"]
    if RequiredCapability.TEXT_MATCH in capabilities:
        result.append("index")
    if capabilities.intersection(_ATTRIBUTE_CAPABILITIES):
        result.append("attribute")
    return result


def _match_plan(
    field: FieldAssessment,
    capabilities: set[RequiredCapability],
    indexing: list[str],
) -> MatchPlan | None:
    if RequiredCapability.TEXT_MATCH in capabilities and "index" in indexing:
        return MatchPlan("text")
    if field.logical_type is LogicalType.STRING and "attribute" in indexing:
        case_claim = field.effective_semantics.get("case_sensitive_exact_match")
        return MatchPlan("word", "cased" if case_claim and case_claim.value is True else None)
    return None


def _unique_transforms(transforms: list[TransformSpec]) -> list[TransformSpec]:
    result: list[TransformSpec] = []
    seen: set[tuple[str, str]] = set()
    for transform in transforms:
        key = (transform.operation, repr(sorted(transform.parameters.items())))
        if key not in seen:
            seen.add(key)
            result.append(transform)
    return result
