"""Elastic-family field semantics recognized by migrate2vespa."""

from __future__ import annotations

import copy
from typing import Any, Iterable

from ...manifest import (
    CardinalityObservation,
    CardinalityState,
    Claim,
    Decision,
    EvidenceLevel,
    FieldAssessment,
    GenerationScope,
    LogicalType,
    QueryAssessment,
    Support,
    TransformSpec,
    value_at_path,
    worst_decision,
)
from ..contract import DecodedSourceDocument
from ..rules import registry_can_generate, registry_decision
from .rules import PATTERN_REGISTRY, USAGE_RULES, effective_field_semantics
from .text_analysis import BUILTIN_LUCENE_ANALYZERS, analysis_config

FIELD_TYPES: dict[str, tuple[LogicalType, str]] = {
    "text": (LogicalType.TEXT, "ES-FIELD-TEXT-001"),
    "keyword": (LogicalType.STRING, "ES-FIELD-KEYWORD-001"),
    "boolean": (LogicalType.BOOLEAN, "ES-FIELD-BOOLEAN-001"),
    "integer": (LogicalType.INTEGER, "ES-FIELD-NUMERIC-001"),
    "long": (LogicalType.LONG, "ES-FIELD-NUMERIC-001"),
    "float": (LogicalType.FLOAT, "ES-FIELD-NUMERIC-001"),
    "double": (LogicalType.DOUBLE, "ES-FIELD-NUMERIC-001"),
    "date": (LogicalType.TEMPORAL, "ES-FIELD-DATE-001"),
    "dense_vector": (LogicalType.VECTOR, "ES-DENSE-VECTOR-001"),
}

COMMON_PARAMETERS = {"type", "fields", "meta", "index", "doc_values"}
TYPE_PARAMETERS = {
    "text": {"analyzer", "search_analyzer", "fielddata"},
    "keyword": {"normalizer"},
    "date": {"format"},
    "dense_vector": {"dims", "similarity"},
    # object/nested: recognize `properties` so the container reports once.
    "object": {"properties"},
    "nested": {"properties"},
}
DATE_FORMATS = {"strict_date_optional_time", "epoch_millis", "epoch_second"}
VECTOR_METRICS = {
    "cosine": "angular",
    "dot_product": "dotproduct",
    "l2_norm": "euclidean",
}


def assess_field(
    name: str,
    props: dict[str, Any],
    documents: list[DecodedSourceDocument],
    context: str | None,
    settings: dict[str, Any] | None = None,
) -> FieldAssessment:
    source_type = str(props.get("type", "object" if "properties" in props else "text"))
    source_path = name.rsplit(".", 1)[0] if context == "multi_field" else name
    reasons: list[str] = []
    risks: list[str] = []
    assumptions: list[str] = []
    unresolved: list[str] = []
    transforms: list[TransformSpec] = []
    rules: list[str] = []
    decision = Decision.DIRECT
    logical_type: LogicalType | None = None
    vector_dimensions: int | None = None
    distance_metric: str | None = None
    safe = True

    definition = FIELD_TYPES.get(source_type)
    inside_unrecognized_container = context not in {None, "multi_field"}
    if definition is None or inside_unrecognized_container:
        rules.append("ES-UNSUPPORTED-001")
        decision = Decision.REVIEW
        safe = False
        if inside_unrecognized_container:
            # The field type may be recognized on its own; the container is not.
            reasons.append(
                f"Field belongs to the {context} structure "
                f"{name.rsplit('.', 1)[0]}, which is not recognized"
            )
        else:
            reasons.append(
                f"Field type or structure {source_type} is not recognized"
            )
    else:
        logical_type, base_rule = definition
        rules.append(base_rule)

    unknown_parameters = sorted(
        set(props) - COMMON_PARAMETERS - TYPE_PARAMETERS.get(source_type, set())
    )
    if unknown_parameters:
        rules.append("ES-UNSUPPORTED-001")
        decision = Decision.REVIEW
        safe = False
        reasons.append(
            "Unrecognized mapping parameters require review: "
            + ", ".join(unknown_parameters)
        )

    if context == "multi_field" and logical_type is not None:
        rules.append("ES-MULTIFIELD-001")
        decision = worst_decision(decision, Decision.ADAPT)
        reasons.append("Multi-field becomes a separate target field sourced from its parent value")

    if props.get("index") is False:
        rules.append("ES-FIELD-INDEX-DISABLED-001")
        decision = worst_decision(decision, Decision.ADAPT)
        reasons.append("index:false is preserved by omitting inverted-index capability")
    if props.get("doc_values") is False:
        rules.append("ES-FIELD-DOCVALUES-DISABLED-001")
        decision = worst_decision(decision, Decision.ADAPT)
        reasons.append("doc_values:false is preserved unless observed usage requires a target attribute")
        if props.get("index") is False:
            rules.append("ES-FIELD-LOOKUP-DISABLED-001")
    if source_type == "text" and props.get("fielddata") is True:
        rules.append("ES-TEXT-FIELDDATA-ENABLED-001")
        decision = worst_decision(decision, Decision.REVIEW)
        risks.append("analyzed_token_access_requires_review")
        reasons.append("Text fielddata semantics require explicit review")

    analyzer_names = [
        str(props[key]) for key in ("analyzer", "search_analyzer") if props.get(key)
    ]
    configured = set((analysis_config(settings or {}).get("analyzer") or {}).keys())
    custom_analyzers = [
        value
        for value in analyzer_names
        if value != "standard" and (value in configured or value not in BUILTIN_LUCENE_ANALYZERS)
    ]
    builtin_analyzers = [
        value
        for value in analyzer_names
        if value in BUILTIN_LUCENE_ANALYZERS and value not in configured
    ]
    if custom_analyzers:
        rules.append("ES-CUSTOM-ANALYZER-001")
        risks.append("linguistic_parity_unknown")
        reasons.append(
            "Custom analysis is referenced; the target field is clear but linguistic "
            "equivalence is not claimed"
        )
    elif builtin_analyzers:
        rules.append("ES-LUCENE-ANALYZER-001")
        decision = worst_decision(decision, Decision.ADAPT)
        risks.append("lucene_linguistics_configuration_required")
        reasons.append("Built-in Lucene analysis requires equivalent Vespa Lucene Linguistics configuration")

    normalizer = props.get("normalizer")
    if normalizer is not None:
        rules.append("ES-KEYWORD-NORMALIZER-001")
        decision = worst_decision(decision, Decision.ADAPT)
        if _is_lowercase_normalizer(str(normalizer), settings or {}):
            transforms.append(TransformSpec("lowercase_string"))
            risks.append("normalizer_implementation_equivalence_unverified")
            reasons.append("Lowercase normalization is reproduced during feed conversion")
        else:
            safe = False
            reasons.append("Referenced normalizer is not recognized for generation")

    raw_values = [value_at_path(document.fields, source_path) for document in documents]
    observation = _cardinality(source_type, raw_values, bool(documents))
    values = [value for value in raw_values if value is not None]
    if observation.state in {CardinalityState.MULTI_OBSERVED, CardinalityState.MIXED_OBSERVED}:
        rules.append(
            "ES-CARDINALITY-MIXED-001"
            if observation.state is CardinalityState.MIXED_OBSERVED
            else "ES-CARDINALITY-MULTI-001"
        )
        decision = worst_decision(decision, Decision.ADAPT)
        reasons.append("Representative documents require an explicit multi-value target field")
    elif logical_type is not LogicalType.VECTOR:
        assumptions.append(
            "source_values_are_scalar"
            if observation.state is CardinalityState.NOT_OBSERVED
            else "unobserved_production_values_match_sample_cardinality"
        )
        unresolved.append(
            "production_cardinality_not_observed"
            if observation.state is CardinalityState.NOT_OBSERVED
            else "production_cardinality_not_proven_by_sample"
        )

    if source_type == "date" and logical_type is not None:
        declared_format = str(props.get("format", "strict_date_optional_time||epoch_millis"))
        if _dual_epoch_date_format(declared_format):
            safe = False
            decision = worst_decision(decision, Decision.REVIEW)
            reasons.append(
                "Date format combines epoch_millis and epoch_second; "
                "Elasticsearch tries formats in declaration order and generation "
                "refuses to guess the unit"
            )
        elif _supported_date_format(declared_format):
            decision = worst_decision(decision, Decision.ADAPT)
            transforms.append(
                TransformSpec("date_to_epoch_seconds", {"declared_format": declared_format})
            )
            risks.append("millisecond_precision_reduced_to_seconds")
            if not values:
                assumptions.append("source_date_values_match_the_declared_format")
                unresolved.append("source_date_value_representation_not_observed")
        else:
            safe = False
            decision = worst_decision(decision, Decision.REVIEW)
            reasons.append(f"Date format is not recognized for generation: {declared_format}")

    if source_type == "dense_vector" and logical_type is not None:
        dims = props.get("dims")
        similarity = props.get("similarity")
        if not isinstance(dims, int) or isinstance(dims, bool) or dims <= 0:
            safe = False
            reasons.append("dense_vector requires a positive declared dims value")
        elif similarity is not None and str(similarity) not in VECTOR_METRICS:
            safe = False
            reasons.append(f"dense_vector similarity is not recognized: {similarity}")
        else:
            vector_dimensions = dims
            distance_metric = VECTOR_METRICS.get(str(similarity), "angular")
            if similarity is None:
                assumptions.append("dense_vector_similarity_is_cosine_for_the_unknown_source_version")
            if "index" not in props:
                assumptions.append("dense_vector_index_is_enabled_for_the_unknown_source_version")
            decision = worst_decision(decision, Decision.ADAPT)
            if assumptions:
                risks.append("vector_defaults_depend_on_unverified_source_version")

    rules = list(dict.fromkeys(rules))
    decision = worst_decision(decision, registry_decision(PATTERN_REGISTRY, rules))
    safe = safe and registry_can_generate(PATTERN_REGISTRY, rules)
    if not safe:
        logical_type = None
        if "ES-UNSUPPORTED-001" not in rules:
            rules.append("ES-UNSUPPORTED-001")

    semantic_rule = next((rule for rule in rules if rule != "ES-UNSUPPORTED-001"), "ES-UNSUPPORTED-001")
    effective = effective_field_semantics(source_type, props, semantic_rule)
    if source_type == "keyword":
        effective["case_sensitive_exact_match"] = Claim(
            value=normalizer is None,
            evidence_level=EvidenceLevel.INFERRED,
            source=f"{semantic_rule}:common-elasticsearch-opensearch-semantics",
            rule=semantic_rule,
        )
    if source_type == "dense_vector":
        similarity_value = props.get("similarity")
        effective["vector_similarity"] = Claim(
            value=str(similarity_value or "cosine"),
            evidence_level=(
                EvidenceLevel.DECLARED
                if similarity_value is not None
                else EvidenceLevel.INFERRED
            ),
            source=(
                f"mapping.properties.{name}.similarity"
                if similarity_value is not None
                else "ES-DENSE-VECTOR-001:version-unverified-default"
            ),
            rule="ES-DENSE-VECTOR-001",
        )

    return FieldAssessment(
        source_name=name,
        source_type=source_type,
        decision=decision,
        reasons=list(dict.fromkeys(reasons)),
        source_properties=copy.deepcopy(props),
        rules=rules,
        source_evidence=_field_evidence(name, props, values, context),
        support=Support(detect=True, generate=safe and logical_type is not None),
        returned_in_documents=bool(values),
        risks=list(dict.fromkeys(risks)),
        generation_note=(
            "Linguistic equivalence is not claimed."
            if custom_analyzers or builtin_analyzers
            else None
        ),
        source_type_claim=Claim(
            value=source_type,
            evidence_level=(
                EvidenceLevel.DECLARED if "type" in props else EvidenceLevel.INFERRED
            ),
            source=_field_path(name, context),
        ),
        effective_semantics=effective,
        cardinality_observation=observation,
        assumptions=list(dict.fromkeys(assumptions)),
        unresolved=list(dict.fromkeys(unresolved)),
        distance_metric=distance_metric,
        logical_type=logical_type,
        source_value_path=source_path,
        value_transforms=transforms,
        vector_dimensions=vector_dimensions,
        vector_cell_type="float" if logical_type is LogicalType.VECTOR else None,
        generation_scope=(
            GenerationScope.SUBTREE
            if context not in {None, "multi_field"}
            or source_type == "object"
            or isinstance(props.get("properties"), dict)
            else GenerationScope.FIELD
        ),
    )


def resolve_query_evidence(
    query: QueryAssessment,
    fields: list[FieldAssessment],
) -> tuple[Decision, list[str]]:
    by_name = {field.source_name: field for field in fields}
    decision = query.decision
    reasons: list[str] = []
    resolved_usage: dict[str, list[str]] = {}
    query_rules: list[str] = []

    for field_name, raw_usages in query.field_usage.items():
        field = by_name.get(field_name)
        if field is None:
            decision = worst_decision(decision, Decision.REVIEW)
            reasons.append(f"Query references unmapped field {field_name}")
            resolved_usage[field_name] = list(raw_usages)
            continue
        usages: set[str] = set()
        for raw_usage in raw_usages:
            resolved, problem = _resolve_usage(raw_usage, field.logical_type)
            declared_problem = _declared_usage_conflict(raw_usage, field)
            if resolved:
                usages.add(resolved)
                rule = USAGE_RULES.get(resolved)
                if rule:
                    query_rules.append(rule)
            if problem:
                decision = worst_decision(decision, Decision.REVIEW)
                reasons.append(f"Field {field_name}: {problem}")
            if declared_problem:
                decision = worst_decision(decision, Decision.REVIEW)
                reasons.append(f"Field {field_name}: {declared_problem}")
        resolved_usage[field_name] = sorted(usages)

    query.field_usage = resolved_usage
    query.observed_fields = sorted(resolved_usage)
    query.rules = list(dict.fromkeys(query.rules + query_rules))
    decision = worst_decision(
        decision,
        registry_decision(PATTERN_REGISTRY, query.rules),
    )
    return decision, list(dict.fromkeys(reasons))


def _resolve_usage(raw: str, logical_type: LogicalType | None) -> tuple[str | None, str | None]:
    if raw == "result_field":
        return "result_field", None
    if raw in {"match_query", "match_phrase_query"}:
        if logical_type is LogicalType.TEXT:
            return "text_search", None
        if logical_type is LogicalType.STRING:
            return "single_token_match", None
        return None, "match semantics do not apply to this field type"
    if raw in {"term_query", "terms_query"}:
        if logical_type is LogicalType.TEXT:
            return "analyzed_term", "term on analyzed text is not whole-value exact matching"
        if logical_type in {
            LogicalType.STRING,
            LogicalType.BOOLEAN,
            LogicalType.INTEGER,
            LogicalType.LONG,
            LogicalType.FLOAT,
            LogicalType.DOUBLE,
            LogicalType.TEMPORAL,
        }:
            return "exact_filter", None
        return None, "exact filtering does not apply to this field type"
    if raw == "range_query":
        if logical_type in {
            LogicalType.INTEGER,
            LogicalType.LONG,
            LogicalType.FLOAT,
            LogicalType.DOUBLE,
            LogicalType.TEMPORAL,
        }:
            return "range_filter", None
        return None, "range filtering requires a numeric or temporal field"
    if raw == "sort":
        if logical_type is LogicalType.TEXT:
            return None, "sorting analyzed text requires an exact-value field"
        return "sort", None
    if raw == "aggregate":
        if logical_type is LogicalType.TEXT:
            return None, "aggregating analyzed text requires an exact-value field"
        return "aggregate", None
    if raw == "vector_retrieval":
        if logical_type is LogicalType.VECTOR:
            return "vector_retrieval", None
        return None, "ANN retrieval requires a dense_vector field"
    return None, f"query usage {raw} is not recognized"


def _declared_usage_conflict(raw: str, field: FieldAssessment) -> str | None:
    indexed = field.effective_semantics.get("indexed")
    doc_values = field.effective_semantics.get("doc_values")
    if raw in {"match_query", "match_phrase_query", "vector_retrieval"} and indexed and indexed.value is False:
        return "representative query requires indexing but the mapping declares index:false"
    if raw in {"sort", "aggregate"} and doc_values and doc_values.value is False:
        return "representative query requires doc-value behavior but the mapping declares doc_values:false"
    if raw in {"term_query", "terms_query", "range_query"} and indexed and doc_values:
        if indexed.value is False and doc_values.value is False:
            return "representative query requires lookup behavior but index and doc_values are disabled"
    return None


def value_compatible(field: FieldAssessment, value: Any) -> bool:
    logical_type = field.logical_type
    if logical_type in {LogicalType.TEXT, LogicalType.STRING}:
        return isinstance(value, str)
    if logical_type is LogicalType.BOOLEAN:
        return isinstance(value, bool)
    if logical_type in {LogicalType.INTEGER, LogicalType.LONG}:
        return isinstance(value, int) and not isinstance(value, bool)
    if logical_type in {LogicalType.FLOAT, LogicalType.DOUBLE}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if logical_type is LogicalType.TEMPORAL:
        return isinstance(value, (str, int, float)) and not isinstance(value, bool)
    if logical_type is LogicalType.VECTOR:
        return (
            isinstance(value, list)
            and len(value) == field.vector_dimensions
            and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value
            )
        )
    return False


def assess_settings(settings: Any) -> tuple[dict[str, Any], list[str]]:
    config = analysis_config(settings)
    analyzers = config.get("analyzer") if isinstance(config.get("analyzer"), dict) else {}
    normalizers = config.get("normalizer") if isinstance(config.get("normalizer"), dict) else {}
    return {
        "analysis": {
            "custom_analyzer_count": len(analyzers),
            "custom_normalizer_count": len(normalizers),
        }
    }, []


def annotate_analysis_evidence(fields: list[FieldAssessment], settings: Any) -> None:
    config = analysis_config(settings)
    for field in fields:
        for key, group in (
            ("analyzer", "analyzer"),
            ("search_analyzer", "analyzer"),
            ("normalizer", "normalizer"),
        ):
            name = field.source_properties.get(key)
            definitions = config.get(group) if isinstance(config.get(group), dict) else {}
            if name in definitions:
                field.source_evidence.append(f"settings.analysis.{group}.{name}")
        field.source_evidence = list(dict.fromkeys(field.source_evidence))


def assess_mapping_options(mapping: dict[str, Any]) -> list[str]:
    current: Any = mapping.get("mappings", mapping)
    if isinstance(current, dict) and len(current) == 1 and "properties" not in current:
        only = next(iter(current.values()))
        if isinstance(only, dict) and "mappings" in only:
            current = only["mappings"]
    if not isinstance(current, dict):
        return []
    unsupported = sorted(set(current) - {"properties", "_meta"})
    return (
        ["Unrecognized mapping options require review: " + ", ".join(unsupported)]
        if unsupported
        else []
    )


def _cardinality(
    source_type: str,
    values: list[Any],
    documents_supplied: bool,
) -> CardinalityObservation:
    def is_array(value: Any) -> bool:
        return isinstance(value, list) and source_type != "dense_vector"

    scalar = sum(value is not None and not is_array(value) for value in values)
    arrays = sum(value is not None and is_array(value) for value in values)
    state = (
        CardinalityState.MIXED_OBSERVED
        if scalar and arrays
        else CardinalityState.MULTI_OBSERVED
        if arrays
        else CardinalityState.SCALAR_ONLY_OBSERVED
        if scalar
        else CardinalityState.NOT_OBSERVED
    )
    return CardinalityObservation(
        state=state,
        documents_examined=len(values),
        scalar_values_seen=scalar,
        array_values_seen=arrays,
        null_or_missing=len(values) - scalar - arrays,
        evidence_level=EvidenceLevel.OBSERVED if documents_supplied else None,
        source="documents.jsonl" if documents_supplied else None,
    )


def _date_format_parts(value: str) -> list[str]:
    return [item.strip() for item in value.split("||") if item.strip()]


def _dual_epoch_date_format(value: str) -> bool:
    parts = _date_format_parts(value)
    return "epoch_millis" in parts and "epoch_second" in parts


def _supported_date_format(value: str) -> bool:
    parts = _date_format_parts(value)
    if not parts or not set(parts).issubset(DATE_FORMATS):
        return False
    if "epoch_millis" in parts and "epoch_second" in parts:
        return False
    return True


def _is_lowercase_normalizer(name: str, settings: dict[str, Any]) -> bool:
    if name == "lowercase":
        return True
    definition = (analysis_config(settings).get("normalizer") or {}).get(name)
    return (
        isinstance(definition, dict)
        and definition.get("type", "custom") == "custom"
        and not definition.get("char_filter")
        and definition.get("filter") == ["lowercase"]
    )


def _field_path(name: str, context: str | None) -> str:
    if context == "multi_field":
        parent, child = name.rsplit(".", 1)
        return f"mapping.properties.{parent}.fields.{child}"
    return f"mapping.properties.{name}"


def _field_evidence(
    name: str,
    props: dict[str, Any],
    values: Iterable[Any],
    context: str | None,
) -> list[str]:
    result = [_field_path(name, context)]
    if any(True for _ in values):
        result.append("documents.jsonl")
    return result
