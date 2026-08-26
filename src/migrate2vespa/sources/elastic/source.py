"""Single Elasticsearch/OpenSearch source implementation.

The shared pipeline sees only :class:`ElasticSource`. Mapping traversal, Query DSL
interpretation, rule selection, settings semantics, and source-value checks remain
private implementation details of this source family.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...manifest import (
    Decision,
    FieldAssessment,
    LogicalType,
    QueryAssessment,
    value_at_path,
    worst_decision,
)
from ..contract import (
    DecodedSourceDocument,
    SourceAssessment,
    SourceInspection,
)
from . import artifacts
from .fields import (
    annotate_analysis_evidence,
    assess_field,
    assess_mapping_options,
    assess_settings,
    resolve_query_evidence,
    value_compatible,
)
from .query import assess_query
from .rules import CARDINALITY_RULES, PATTERN_REGISTRY, USAGE_RULES
from .text_analysis import assess_text_analysis


class ElasticSource:
    """Interpret common Elasticsearch/OpenSearch artifacts."""

    id = "elastic-family"
    registry = PATTERN_REGISTRY

    def inspect(self, input_directory: Path) -> SourceInspection:
        return artifacts.inspect(input_directory, source_id=self.id)

    def decode_document(
        self,
        raw: dict[str, Any],
        sequence: int,
        source_path: str,
    ) -> DecodedSourceDocument:
        return artifacts.decode_document(raw, sequence, source_path)

    def assess(self, inspection: SourceInspection) -> SourceAssessment:
        fields = [
            assess_field(
                name,
                properties,
                inspection.documents,
                context,
                inspection.settings,
            )
            for name, properties, context in inspection.field_declarations
        ]
        annotate_analysis_evidence(fields, inspection.settings)

        queries = _assess_queries(inspection, fields)
        _attach_query_usage(fields, queries)
        _attach_evidence_rules(fields)

        settings_summary, settings_issues = assess_settings(
            inspection.settings
        )
        source_signals = assess_text_analysis(fields, inspection.settings)

        return SourceAssessment(
            fields=fields,
            queries=queries,
            settings_summary=settings_summary,
            source_signals=source_signals,
            mapping_issues=assess_mapping_options(inspection.mapping),
            settings_issues=settings_issues,
            document_issues=(
                list(inspection.document_issues)
                + _document_consistency_issues(fields, inspection.documents)
            ),
        )


def _assess_queries(
    inspection: SourceInspection,
    fields: list[FieldAssessment],
) -> list[QueryAssessment]:
    queries: list[QueryAssessment] = []
    for artifact in inspection.queries:
        if artifact.error is not None or artifact.body is None:
            queries.append(QueryAssessment(
                name=artifact.query_id,
                source_file=artifact.source_file,
                decision=Decision.REVIEW,
                reasons=[f"Invalid query file: {artifact.error}"],
                source_constructs=["INVALID"],
                migration_signals=["invalid_query"],
                source_evidence=[artifact.source_file],
            ))
            continue
        query = assess_query(artifact.query_id, artifact.source_file, artifact.body)
        decision, reasons = resolve_query_evidence(query, fields)
        query.decision = worst_decision(query.decision, decision)
        query.reasons = list(dict.fromkeys(query.reasons + reasons))
        queries.append(query)
    return queries


def _attach_query_usage(
    fields: list[FieldAssessment],
    queries: list[QueryAssessment],
) -> None:
    fields_by_name = {field.source_name: field for field in fields}
    for query in queries:
        for field_name, usages in query.field_usage.items():
            field = fields_by_name.get(field_name)
            if field is None:
                continue
            field.observed_usage = sorted(set(field.observed_usage).union(usages))
            for usage in usages:
                field.usage_sources.setdefault(usage, []).append(query.source_file)
                field.usage_sources[usage] = sorted(set(field.usage_sources[usage]))


def _attach_evidence_rules(fields: list[FieldAssessment]) -> None:
    for field in fields:
        cardinality_rule = CARDINALITY_RULES.get(
            field.cardinality_observation.state.value
        )
        if cardinality_rule:
            field.rules.append(cardinality_rule)
        for usage in field.observed_usage:
            usage_rule = USAGE_RULES.get(usage)
            if usage_rule:
                field.rules.append(usage_rule)
        field.rules = list(dict.fromkeys(field.rules))


def _document_consistency_issues(
    fields: list[FieldAssessment],
    documents: list[DecodedSourceDocument],
) -> list[str]:
    issues: list[str] = []
    for document_number, document in enumerate(documents, 1):
        for field in fields:
            if not field.support.generate or field.logical_type is None:
                continue
            source_path = field.source_value_path or field.source_name
            value = value_at_path(document.fields, source_path)
            values = (
                [value]
                if field.logical_type is LogicalType.VECTOR
                else value
                if isinstance(value, list)
                else [value]
            )
            for item in values:
                if item is None or value_compatible(field, item):
                    continue
                issues.append(
                    f"Document {document_number} field {field.source_name} has value "
                    f"incompatible with {field.source_type}; document type is inconsistent"
                )
    return list(dict.fromkeys(issues))
