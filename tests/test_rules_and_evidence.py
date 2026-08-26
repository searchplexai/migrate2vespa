from __future__ import annotations

import pytest

from migrate2vespa.manifest import (
    CardinalityState,
    Decision,
    LogicalType,
    TransformSpec,
)
from migrate2vespa.sources.contract import DecodedSourceDocument
from migrate2vespa.sources.elastic.fields import assess_field, resolve_query_evidence
from migrate2vespa.sources.elastic.query import assess_query
from migrate2vespa.sources.elastic.rules import PATTERN_REGISTRY
from migrate2vespa.target.planner import apply_plan
from migrate2vespa.target.transforms import TransformError, apply_transforms

def document(value):
    return DecodedSourceDocument("1", value, "documents.jsonl", 1)


def test_registry_entries_are_complete_and_consistent():
    assert all(rule.id == key for key, rule in PATTERN_REGISTRY.items())
    assert all(rule.fixture and rule.rationale for rule in PATTERN_REGISTRY.values())


@pytest.mark.parametrize(
    ("properties", "settings", "expected_rule", "expected_decision", "generates"),
    [
        ({"type": "text", "analyzer": "custom_text"}, {}, "ES-CUSTOM-ANALYZER-001", Decision.DIRECT, True),
        ({"type": "text", "analyzer": "english"}, {}, "ES-LUCENE-ANALYZER-001", Decision.ADAPT, True),
        ({"type": "text", "index": False}, {}, "ES-FIELD-INDEX-DISABLED-001", Decision.ADAPT, True),
        ({"type": "keyword", "doc_values": False}, {}, "ES-FIELD-DOCVALUES-DISABLED-001", Decision.ADAPT, True),
        ({"type": "keyword", "index": False, "doc_values": False}, {}, "ES-FIELD-LOOKUP-DISABLED-001", Decision.ADAPT, True),
        ({"type": "text", "fielddata": True}, {}, "ES-TEXT-FIELDDATA-ENABLED-001", Decision.REVIEW, True),
        ({"type": "keyword", "normalizer": "unknown"}, {}, "ES-KEYWORD-NORMALIZER-001", Decision.ADAPT, False),
    ],
)
def test_mapping_semantic_rules_have_explicit_decision_and_generation_support(
    properties, settings, expected_rule, expected_decision, generates
):
    field = assess_field("value", properties, [], None, settings)
    assert expected_rule in field.rules
    assert PATTERN_REGISTRY[expected_rule].decision is expected_decision
    assert field.support.generate is generates


def test_mixed_cardinality_rule_is_recognized():
    field = assess_field(
        "tags",
        {"type": "keyword"},
        [document({"tags": "one"}), DecodedSourceDocument("2", {"tags": ["two"]}, "documents.jsonl", 2)],
        None,
        {},
    )
    assert field.cardinality_observation.state is CardinalityState.MIXED_OBSERVED
    assert "ES-CARDINALITY-MIXED-001" in field.rules


@pytest.mark.parametrize(
    ("source_type", "logical_type", "target_type"),
    [
        ("text", LogicalType.TEXT, "string"),
        ("keyword", LogicalType.STRING, "string"),
        ("boolean", LogicalType.BOOLEAN, "bool"),
        ("integer", LogicalType.INTEGER, "int"),
        ("long", LogicalType.LONG, "long"),
        ("float", LogicalType.FLOAT, "float"),
        ("double", LogicalType.DOUBLE, "double"),
    ],
)
def test_core_fields_produce_safe_target_plans(source_type, logical_type, target_type):
    field = assess_field("value", {"type": source_type}, [], None, {})
    field.required_capabilities = sorted(
        {
            capability
            for rule_id in field.rules
            for capability in PATTERN_REGISTRY[rule_id].capabilities
        },
        key=lambda item: item.value,
    )

    assert field.logical_type is logical_type
    assert field.support.generate
    assert apply_plan(field) == ()
    assert field.target_plan.type == target_type


def test_unsupported_field_and_parameter_have_no_target_plan():
    unknown = assess_field("mystery", {"type": "plugin_field"}, [], None, {})
    parameter = assess_field(
        "sku", {"type": "keyword", "future_semantic": True}, [], None, {}
    )

    assert unknown.rules == ["ES-UNSUPPORTED-001"]
    assert not unknown.support.generate and unknown.logical_type is None
    assert "ES-UNSUPPORTED-001" in parameter.rules
    assert not parameter.support.generate and parameter.logical_type is None


def test_multifield_and_observed_array_are_explicit_adaptations():
    multi = assess_field(
        "title.raw", {"type": "keyword"}, [document({"title": "Hello"})], "multi_field", {}
    )
    array = assess_field(
        "tags", {"type": "keyword"}, [document({"tags": ["one", "two"]})], None, {}
    )

    assert multi.source_value_path == "title"
    assert "ES-MULTIFIELD-001" in multi.rules
    assert multi.decision is Decision.ADAPT
    assert array.cardinality_observation.state is CardinalityState.MULTI_OBSERVED
    assert "ES-CARDINALITY-MULTI-001" in array.rules


def test_supported_date_and_lowercase_normalizer_have_typed_transforms():
    settings = {
        "settings": {
            "analysis": {
                "normalizer": {
                    "folded": {"type": "custom", "filter": ["lowercase"]}
                }
            }
        }
    }
    date = assess_field(
        "created", {"type": "date", "format": "strict_date_optional_time||epoch_millis"},
        [document({"created": "2026-01-01T00:00:00Z"})], None, settings
    )
    keyword = assess_field("sku", {"type": "keyword", "normalizer": "folded"}, [], None, settings)

    assert [item.operation for item in date.value_transforms] == ["date_to_epoch_seconds"]
    assert [item.operation for item in keyword.value_transforms] == ["lowercase_string"]
    assert date.support.generate and keyword.support.generate


def test_ambiguous_date_and_non_float_vector_are_generic_unsupported():
    date = assess_field("created", {"type": "date", "format": "yyyy/MM/dd"}, [], None, {})
    vector = assess_field(
        "embedding", {"type": "dense_vector", "dims": 3, "element_type": "byte"}, [], None, {}
    )
    assert not date.support.generate and "ES-UNSUPPORTED-001" in date.rules
    assert not vector.support.generate and "ES-UNSUPPORTED-001" in vector.rules


def test_dual_epoch_date_format_is_rejected_and_ordered_units_parse():
    field = assess_field(
        "created", {"type": "date", "format": "epoch_millis||epoch_second"}, [], None, {}
    )
    assert not field.support.generate
    assert "epoch_millis and epoch_second" in field.reasons[0]
    assert apply_transforms(
        1420070400000,
        (TransformSpec("date_to_epoch_seconds", {"declared_format": "epoch_millis"}),),
    ) == 1420070400
    assert apply_transforms(
        1420070400,
        (TransformSpec("date_to_epoch_seconds", {"declared_format": "epoch_second"}),),
    ) == 1420070400
    with pytest.raises(TransformError):
        apply_transforms(
            1420070400,
            (
                TransformSpec(
                    "date_to_epoch_seconds",
                    {"declared_format": "epoch_millis||epoch_second"},
                ),
            ),
        )


def test_float_vector_records_version_unverified_defaults():
    field = assess_field("embedding", {"type": "dense_vector", "dims": 3}, [], None, {})
    assert field.vector_cell_type == "float"
    assert field.distance_metric == "angular"
    assert len(field.assumptions) == 2
    assert field.support.generate


@pytest.mark.parametrize(
    ("source_type", "operator", "expected_usage", "expected_rule", "expected_decision"),
    [
        ("text", "match", "text_search", "ES-USAGE-TEXT-SEARCH-001", Decision.DIRECT),
        ("keyword", "match", "single_token_match", "ES-USAGE-SINGLE-TOKEN-MATCH-001", Decision.ADAPT),
        ("keyword", "term", "exact_filter", "ES-USAGE-EXACT-FILTER-001", Decision.DIRECT),
        ("text", "term", "analyzed_term", "ES-USAGE-ANALYZED-TERM-001", Decision.REVIEW),
    ],
)
def test_query_operators_are_interpreted_with_field_semantics(
    source_type, operator, expected_usage, expected_rule, expected_decision
):
    field = assess_field("value", {"type": source_type}, [], None, {})
    query = assess_query("q", "queries/q.json", {"query": {operator: {"value": "x"}}})
    decision, _ = resolve_query_evidence(query, [field])
    assert query.field_usage["value"] == [expected_usage]
    assert expected_rule in query.rules
    assert decision is expected_decision


@pytest.mark.parametrize(
    ("field", "body", "expected_rule"),
    [
        (assess_field("price", {"type": "double"}, [], None, {}), {"query": {"range": {"price": {"lte": 10}}}}, "ES-USAGE-RANGE-001"),
        (assess_field("sku", {"type": "keyword"}, [], None, {}), {"sort": ["sku"]}, "ES-USAGE-SORT-001"),
        (assess_field("sku", {"type": "keyword"}, [], None, {}), {"aggs": {"values": {"terms": {"field": "sku"}}}}, "ES-USAGE-AGGREGATE-001"),
        (assess_field("embedding", {"type": "dense_vector", "dims": 2}, [], None, {}), {"knn": {"field": "embedding", "query_vector": [0.1, 0.2], "k": 2, "num_candidates": 5}}, "ES-USAGE-ANN-001"),
        (
            assess_field("embedding", {"type": "dense_vector", "dims": 2}, [], None, {}),
            {"knn": [{"field": "embedding", "query_vector": [0.1, 0.2], "k": 2, "num_candidates": 5}]},
            "ES-USAGE-ANN-001",
        ),
    ],
)
def test_remaining_usage_rules_are_selected(field, body, expected_rule):
    query = assess_query("q", "queries/q.json", body)
    resolve_query_evidence(query, [field])
    assert expected_rule in query.rules
    if "knn" in body:
        assert query.field_usage["embedding"] == ["vector_retrieval"]


def test_meaningful_sort_and_leaf_options_require_review():
    query = assess_query(
        "q",
        "queries/q.json",
        {
            "query": {"match": {"title": {"query": "x", "operator": "and"}}},
            "sort": [{"price": {"order": "asc", "missing": "_last"}}],
        },
    )
    assert query.decision is Decision.REVIEW
    assert query.sort_evidence[0]["options"] == {"missing": "_last"}
    assert "query_options_require_review" in query.migration_signals


def test_aggregation_evidence_and_custom_scoring():
    plain = assess_query(
        "a", "queries/a.json", {"aggs": {"brands": {"terms": {"field": "brand"}}}}
    )
    rich = assess_query(
        "b",
        "queries/b.json",
        {
            "aggs": {
                "brands": {
                    "terms": {"field": "brand", "size": 20, "order": {"_count": "desc"}},
                }
            }
        },
    )
    scoring = assess_query(
        "s", "queries/s.json", {"query": {"function_score": {"query": {"match_all": {}}}}}
    )
    missing_knn = assess_query(
        "k", "queries/k.json", {"knn": [{"query_vector": [0.1, 0.2], "k": 2}]}
    )
    assert plain.decision is Decision.ADAPT
    assert plain.field_usage["brand"] == ["aggregate"]
    assert rich.decision is Decision.REVIEW
    assert "aggregation_requires_review" in rich.migration_signals
    assert scoring.decision is Decision.REDESIGN
    assert "custom_scoring" in scoring.migration_signals
    assert missing_knn.decision is Decision.REVIEW
    assert "knn_field_unresolved" in missing_knn.migration_signals
