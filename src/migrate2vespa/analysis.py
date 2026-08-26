from __future__ import annotations

import os
from pathlib import Path

from . import __version__
from .io import sanitize_name
from .manifest import (
    CardinalityState,
    CoverageLedger,
    Decision,
    FieldAssessment,
    GenerationScope,
    MigrationManifest,
    RequiredCapability,
    Support,
    worst_decision,
)
from .sources.contract import Source, SourceInspection
from .sources.rules import registry_capabilities, registry_decision
from .target.planner import apply_plan
from .target.selection import build_generation_plan


def analyze_inspection(
    source: Source,
    inspection: SourceInspection,
    input_directory: Path,
    output_directory: Path,
) -> MigrationManifest:
    documents = inspection.documents
    artifact_inventory = {item.kind: item for item in inspection.artifacts}
    synthetic_document_id_count = sum(document.synthetic_id for document in documents)
    source_assessment = source.assess(inspection)
    fields = source_assessment.fields
    queries = source_assessment.queries

    for field in fields:
        if not queries:
            field.unresolved.append("field_usage_not_observed")
            field.confidence["usage"] = "LOW"
        elif field.usage_sources:
            field.confidence["usage"] = "MEDIUM"
        else:
            field.unresolved.append("field_not_referenced_by_representative_queries")
            field.confidence["usage"] = "LOW"
        field.decision = worst_decision(
            field.decision,
            registry_decision(source.registry, field.rules),
        )
        field.required_capabilities = registry_capabilities(
            source.registry,
            field.rules,
        )
        if field.support.generate:
            _apply_planning_issues(field, apply_plan(field))
        _refresh_field_confidence(field)

    _omit_target_name_collisions(fields)
    document_issues = source_assessment.document_issues
    cardinality_issues = [
        "Sample documents do not prove production cardinality"
        if documents
        else "Field cardinality cannot be determined from mappings alone; review scalar/array choices"
    ]
    if synthetic_document_id_count:
        cardinality_issues.append(
            f"{synthetic_document_id_count} document(s) have no _id or id; deterministic synthetic IDs will be generated"
        )
    unresolved = (
        source_assessment.mapping_issues
        + source_assessment.settings_issues
        + cardinality_issues
        + document_issues
        + [
            f"Field {field.source_name}: {reason}"
            for field in fields
            if field.decision in {Decision.REVIEW, Decision.REDESIGN}
            for reason in field.reasons
        ]
        + [
            f"Query {query.name}: {reason}"
            for query in queries
            if query.decision in {Decision.REVIEW, Decision.REDESIGN}
            for reason in query.reasons
        ]
    )

    unresolved_unique = list(dict.fromkeys(unresolved))
    blocking_issues = {
        f"Field {field.source_name}: {reason}"
        for field in fields
        if not field.support.generate
        for reason in field.reasons
    }
    blocking_issues.update(document_issues)
    blockers = [item for item in unresolved_unique if item in blocking_issues]
    warnings = [item for item in unresolved_unique if item not in blockers]
    field_aliases = {
        field.source_name: field.target_plan.name
        for field in fields
        if field.target_plan is not None and field.source_name != field.target_plan.name
    }
    coverage = CoverageLedger(
        fields_supplied=artifact_inventory["fields"].supplied_count,
        fields_assessed=len(fields),
        fields_unassessed=artifact_inventory["fields"].supplied_count - len(fields),
        queries_supplied=artifact_inventory["queries"].supplied_count,
        queries_assessed=sum("invalid_query" not in query.migration_signals for query in queries),
        queries_invalid=sum("invalid_query" in query.migration_signals for query in queries),
        queries_unassessed=artifact_inventory["queries"].supplied_count - len(queries),
        documents_supplied=artifact_inventory["documents"].supplied_count,
        documents_examined=len(documents),
        documents_invalid=artifact_inventory["documents"].invalid_count,
    )
    if not coverage.all_supplied_accounted_for:
        blockers.append("Artifact coverage is incomplete; at least one supplied item has no assessment outcome")
    manifest = MigrationManifest(
        tool_version=__version__,
        project_name=sanitize_name(inspection.index_name),
        input_directory=portable_input_path(input_directory, output_directory),
        fields=fields,
        queries=queries,
        settings=source_assessment.settings_summary,
        blockers=blockers,
        warnings=warnings,
        document_count=len(documents),
        document_digest=inspection.document_digest,
        synthetic_document_id_count=synthetic_document_id_count,
        settings_present=inspection.settings_present,
        mapping_file=inspection.mapping_file,
        settings_file=inspection.settings_file,
        field_aliases=field_aliases,
        validation={
            "analysis": "COMPLETE" if coverage.all_supplied_accounted_for else "INCOMPLETE",
            "generation": "NOT_RUN",
        },
        coverage=coverage,
        source_id=inspection.source_id,
        source_index_name=inspection.source_index_name,
        source_signals=source_assessment.source_signals,
    )
    manifest.generation = build_generation_plan(manifest)
    return manifest


def _refresh_field_confidence(field: FieldAssessment) -> None:
    state = field.cardinality_observation.state
    field.confidence.update({
        "target_representation": (
            "HIGH" if field.decision is Decision.DIRECT and field.target_plan is not None
            else "MEDIUM" if field.decision is Decision.ADAPT and field.target_plan is not None
            else "LOW"
        ),
        "cardinality": (
            "HIGH" if state in {CardinalityState.MULTI_OBSERVED, CardinalityState.MIXED_OBSERVED}
            else "MEDIUM" if state is CardinalityState.SCALAR_ONLY_OBSERVED
            else "LOW"
        ),
        "behavioral_equivalence": (
            "UNKNOWN" if not field.usage_sources
            else "LOW" if field.decision in {Decision.REVIEW, Decision.REDESIGN} or "linguistic_parity_unknown" in field.risks
            else "MEDIUM" if field.decision is Decision.ADAPT or RequiredCapability.TEXT_MATCH in field.required_capabilities
            else "HIGH"
        ),
    })


def _apply_planning_issues(field: FieldAssessment, issues: tuple[str, ...]) -> None:
    """Apply source-independent planner failures to the migration assessment."""
    if not issues:
        if field.target_plan and field.target_plan.name != field.source_name.replace(".", "__").replace("-", "_"):
            field.decision = worst_decision(field.decision, Decision.ADAPT)
            field.reasons.append(
                f"Source field name requires the Vespa-safe identifier {field.target_plan.name}"
            )
        return
    field.decision = worst_decision(field.decision, Decision.REVIEW)
    field.reasons.extend(issues)
    field.risks.append("required_capability_unmet")
    field.support.generate = False
    field.reasons = list(dict.fromkeys(field.reasons))
    field.risks = list(dict.fromkeys(field.risks))


def _omit_target_name_collisions(fields: list[FieldAssessment]) -> None:
    """Conservatively omit fields whose normalized Vespa names collide."""
    by_target: dict[str, list[FieldAssessment]] = {}
    for field in fields:
        if field.target_plan is not None:
            by_target.setdefault(field.target_plan.name, []).append(field)
    for target_name, matches in by_target.items():
        if len(matches) < 2:
            continue
        source_names = ", ".join(field.source_name for field in matches)
        for field in matches:
            field.decision = worst_decision(field.decision, Decision.REVIEW)
            field.support = Support(detect=True, generate=False)
            field.generation_scope = GenerationScope.PACKAGE
            field.target_plan = None
            field.reasons.append(
                f"Vespa-safe name {target_name} collides for source fields: {source_names}"
            )


def portable_input_path(input_directory: Path, output_directory: Path) -> str:
    """Return a manifest-relative path to the supplied source directory."""
    relative = os.path.relpath(input_directory.resolve(), output_directory.resolve())
    return relative or "."
