"""Conservative Query DSL evidence extraction for the v0.1 supported subset."""

from __future__ import annotations

from typing import Any

from ...manifest import Decision, QueryAssessment, worst_decision


KNOWN_QUERY_OPERATORS = {
    "bool",
    "function_score",
    "knn",
    "match",
    "match_all",
    "match_phrase",
    "range",
    "script_score",
    "term",
    "terms",
}

BASIC_AGGREGATIONS = {
    "avg",
    "cardinality",
    "date_histogram",
    "histogram",
    "max",
    "min",
    "range",
    "stats",
    "sum",
    "terms",
    "value_count",
}


def assess_query(
    name: str,
    source_file: str,
    body: dict[str, Any],
) -> QueryAssessment:
    """Inventory migration-relevant evidence without producing YQL."""
    clause = body.get("query", {"knn": body["knn"]} if "knn" in body else {"match_all": {}})
    constructs, field_usage = collect_query_evidence(clause)
    unknown = _unknown_query_operators(clause)
    reasons: list[str] = []
    signals: list[str] = []
    risks: list[str] = []
    decision = Decision.DIRECT

    source_filter = body.get("_source")
    if isinstance(source_filter, list):
        for field_name in source_filter:
            field_usage.setdefault(str(field_name), set()).add("result_field")
    elif isinstance(source_filter, dict):
        for field_name in source_filter.get("includes", []):
            field_usage.setdefault(str(field_name), set()).add("result_field")

    sort_evidence, sort_reasons = _sort_evidence(body.get("sort"))
    for item in sort_evidence:
        field_name = item.get("field")
        if isinstance(field_name, str) and not field_name.startswith("_"):
            field_usage.setdefault(field_name, set()).add("sort")
    if sort_reasons:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("sort_options_require_review")
        reasons.extend(sort_reasons)
        risks.append("sort_semantics_require_review")

    aggregation_fields, aggregation_types, aggregation_problems = _aggregation_evidence(body)
    for field_name in aggregation_fields:
        field_usage.setdefault(field_name, set()).add("aggregate")
    constructs.extend(f"aggregation:{item}" for item in aggregation_types)
    if aggregation_types or aggregation_problems:
        decision = worst_decision(decision, Decision.ADAPT)
        signals.append("aggregation")
        reasons.append("Aggregation usage establishes target grouping requirements but is not translated")
    if aggregation_problems:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("aggregation_requires_review")
        reasons.extend(aggregation_problems)
        risks.append("aggregation_semantics_require_review")

    if any(item in constructs for item in {"function_score", "script_score"}):
        decision = Decision.REDESIGN
        signals.append("custom_scoring")
        reasons.append("Custom scoring is ranking policy and requires an explicit Vespa rank-profile design")
        risks.append("ranking_policy_requires_redesign")
    if "knn" in constructs:
        decision = worst_decision(decision, Decision.ADAPT)
        signals.append("ann_retrieval")
        reasons.append("ANN retrieval requires an indexed tensor and an explicit Vespa query design")

    bool_options = _bool_options(clause)
    if bool_options:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("bool_options_require_review")
        reasons.append("Boolean query options require review: " + ", ".join(bool_options))
        risks.append("boolean_matching_semantics_require_review")
    query_options = _leaf_options(clause)
    if query_options:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("query_options_require_review")
        reasons.append("Query options require review: " + ", ".join(query_options))
        risks.append("query_operator_semantics_require_review")
    if unknown:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("unknown_query_construct")
        reasons.append("Query DSL operators outside the v0.1 subset require review: " + ", ".join(unknown))

    allowed_top_level = {
        "query", "knn", "size", "from", "sort", "_source", "track_total_hits",
        "aggs", "aggregations",
    }
    unsupported_options = sorted(set(body) - allowed_top_level)
    if unsupported_options:
        decision = worst_decision(decision, Decision.REVIEW)
        signals.append("unsupported_search_option")
        reasons.append("Search request options require review: " + ", ".join(unsupported_options))

    return QueryAssessment(
        name=name,
        source_file=source_file,
        decision=decision,
        reasons=_dedupe(reasons),
        source_constructs=_dedupe(constructs),
        observed_fields=sorted(field_usage),
        field_usage={field: sorted(usages) for field, usages in sorted(field_usage.items())},
        migration_signals=_dedupe(signals),
        source_evidence=[source_file],
        risks=_dedupe(risks),
        sort_evidence=sort_evidence,
    )


def collect_query_evidence(clause: Any) -> tuple[list[str], dict[str, set[str]]]:
    constructs: list[str] = []
    usage: dict[str, set[str]] = {}
    field_operators = {
        "term": "term_query",
        "terms": "terms_query",
        "range": "range_query",
        "match": "match_query",
        "match_phrase": "match_phrase_query",
    }

    def add(field_name: str, use: str) -> None:
        usage.setdefault(field_name, set()).add(use)

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for operator, child in value.items():
            if operator in KNOWN_QUERY_OPERATORS:
                constructs.append(operator)
            if operator in field_operators and isinstance(child, dict):
                for field_name in child:
                    add(str(field_name), field_operators[operator])
                continue
            if operator == "knn" and isinstance(child, dict) and isinstance(child.get("field"), str):
                add(child["field"], "vector_retrieval")
                continue
            if operator == "bool" and isinstance(child, dict):
                for key in ("must", "filter", "should", "must_not"):
                    visit(child.get(key))
                continue
            if operator in {"function_score", "script_score"} and isinstance(child, dict):
                visit(child.get("query"))
                continue
            visit(child)

    visit(clause)
    return _dedupe(constructs), usage


def _unknown_query_operators(clause: Any) -> list[str]:
    unknown: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for operator, child in value.items():
            if operator not in KNOWN_QUERY_OPERATORS:
                unknown.add(str(operator))
                continue
            if operator == "bool" and isinstance(child, dict):
                for key in ("must", "filter", "should", "must_not"):
                    visit(child.get(key))
            elif operator in {"function_score", "script_score"} and isinstance(child, dict):
                visit(child.get("query"))

    visit(clause)
    return sorted(unknown)


def _bool_options(clause: Any) -> list[str]:
    findings: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for operator, child in value.items():
            if operator == "bool" and isinstance(child, dict):
                for option in sorted(set(child) - {"must", "filter", "should", "must_not"}):
                    findings.append(f"bool.{option}")
                for key in ("must", "filter", "should", "must_not"):
                    visit(child.get(key))
            elif operator in {"function_score", "script_score"} and isinstance(child, dict):
                visit(child.get("query"))

    visit(clause)
    return _dedupe(findings)


def _leaf_options(clause: Any) -> list[str]:
    findings: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        for operator, child in value.items():
            if operator in {"match", "match_phrase"} and isinstance(child, dict):
                for field_name, definition in child.items():
                    if isinstance(definition, dict):
                        options = sorted(set(definition) - {"query"})
                        if options:
                            findings.append(f"{operator}.{field_name} ({', '.join(options)})")
                continue
            if operator in {"term", "terms"} and isinstance(child, dict):
                for field_name, definition in child.items():
                    if isinstance(definition, dict):
                        allowed = {"value"} if operator == "term" else {"index"}
                        options = sorted(set(definition) - allowed)
                        if options:
                            findings.append(
                                f"{operator}.{field_name} ({', '.join(options)})"
                            )
                continue
            if operator == "range" and isinstance(child, dict):
                for field_name, definition in child.items():
                    if isinstance(definition, dict):
                        options = sorted(set(definition) - {"gt", "gte", "lt", "lte"})
                        if options:
                            findings.append(f"range.{field_name} ({', '.join(options)})")
                continue
            if operator == "knn" and isinstance(child, dict):
                options = sorted(set(child) - {"field", "query_vector", "k", "num_candidates"})
                if options:
                    findings.append(f"knn ({', '.join(options)})")
                continue
            if operator == "bool" and isinstance(child, dict):
                for key in ("must", "filter", "should", "must_not"):
                    visit(child.get(key))
            elif operator in {"function_score", "script_score"} and isinstance(child, dict):
                visit(child.get("query"))

    visit(clause)
    return _dedupe(findings)


def _sort_evidence(sort: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if sort is None:
        return [], []
    values = sort if isinstance(sort, list) else [sort]
    evidence: list[dict[str, Any]] = []
    reasons: list[str] = []
    for item in values:
        if isinstance(item, str):
            evidence.append({"field": item, "order": "asc", "options": {}})
            continue
        if not isinstance(item, dict) or len(item) != 1:
            reasons.append("sort contains an unsupported definition")
            continue
        field_name, definition = next(iter(item.items()))
        if isinstance(definition, str):
            evidence.append({"field": str(field_name), "order": definition, "options": {}})
            if definition not in {"asc", "desc"}:
                reasons.append(f"sort.{field_name} uses unsupported order {definition}")
            continue
        if not isinstance(definition, dict):
            reasons.append(f"sort.{field_name} has an unsupported definition")
            continue
        order = str(definition.get("order", "asc"))
        options = {str(key): value for key, value in definition.items() if key != "order"}
        evidence.append({"field": str(field_name), "order": order, "options": options})
        if order not in {"asc", "desc"} or options:
            details = sorted(([f"order={order}"] if order not in {"asc", "desc"} else []) + list(options))
            reasons.append(f"sort.{field_name} requires review: {', '.join(details)}")
    return evidence, reasons


def _aggregation_evidence(body: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    root = body.get("aggs", body.get("aggregations"))
    if root is None:
        return [], [], []
    if not isinstance(root, dict):
        return [], [], ["aggregations must be an object"]
    fields: set[str] = set()
    types: list[str] = []
    problems: list[str] = []

    def visit(aggregations: dict[str, Any]) -> None:
        for name, definition in aggregations.items():
            if not isinstance(definition, dict):
                problems.append(f"aggregation {name} has an invalid definition")
                continue
            operators = [key for key in definition if key not in {"aggs", "aggregations", "meta"}]
            if len(operators) != 1:
                problems.append(f"aggregation {name} does not identify one supported type")
            for operator in operators:
                types.append(operator)
                config = definition.get(operator)
                if operator not in BASIC_AGGREGATIONS:
                    problems.append(f"aggregation type {operator} is outside the v0.1 subset")
                if isinstance(config, dict) and isinstance(config.get("field"), str):
                    fields.add(config["field"])
            child = definition.get("aggs", definition.get("aggregations"))
            if isinstance(child, dict):
                visit(child)

    visit(root)
    return sorted(fields), _dedupe(types), _dedupe(problems)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
