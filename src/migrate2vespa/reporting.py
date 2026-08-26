from __future__ import annotations

from .manifest import Decision, MigrationManifest, OperationalStatus

CONSOLE_FIELD_STATUS_LABELS = {
    OperationalStatus.READY: "READY",
    OperationalStatus.READY_WITH_CAVEATS: "READY WITH CAVEATS",
    OperationalStatus.BLOCKED_DECISION: "DECISION REQUIRED",
    OperationalStatus.BLOCKED_REDESIGN: "REDESIGN REQUIRED",
}

DIAGNOSTIC_KINDS = frozenset({
    "FIELD",
    "ANALYZER",
    "NORMALIZER",
    "QUERY",
    "SETTING",
})


def _sentence(text: str) -> str:
    value = " ".join(str(text).strip().split())
    if not value:
        return value
    if value[0].islower():
        value = value[0].upper() + value[1:]
    return value if value[-1] in ".!?" else value + "."


def _attention_text(item: object) -> str:
    reasons = [str(value).strip() for value in getattr(item, "reasons", []) if str(value).strip()]
    risks = [str(value).strip() for value in getattr(item, "risks", []) if str(value).strip()]
    if reasons:
        return _sentence(reasons[0].split(";", 1)[0])
    if risks:
        return _sentence(risks[0].replace("_", " "))
    return "Human review is required."


def _field_status(field: object) -> OperationalStatus:
    if not field.support.generate:
        return (
            OperationalStatus.BLOCKED_REDESIGN
            if field.decision is Decision.REDESIGN
            else OperationalStatus.BLOCKED_DECISION
        )
    if field.decision is Decision.REVIEW or field.risks:
        return OperationalStatus.READY_WITH_CAVEATS
    return OperationalStatus.READY


def _query_status(query: object) -> OperationalStatus:
    if query.decision is Decision.DIRECT and getattr(query, "risks", []):
        return OperationalStatus.READY_WITH_CAVEATS
    return {
        Decision.DIRECT: OperationalStatus.READY,
        Decision.ADAPT: OperationalStatus.READY_WITH_CAVEATS,
        Decision.REVIEW: OperationalStatus.BLOCKED_DECISION,
        Decision.REDESIGN: OperationalStatus.BLOCKED_REDESIGN,
    }[query.decision]


def _console_count(label: str, count: int, *, width: int = 28) -> str:
    return f"  {label:<{width}}{count:>3}"


def _field_source_path(field: object) -> str:
    claim = getattr(field, "source_type_claim", None)
    source = str(getattr(claim, "source", "") or "")
    return source.removeprefix("mapping.") or f"properties.{field.source_name}"


def _diagnostic_row(kind: str, path: str, message: str) -> str:
    return f"  {kind:<12}{path:<34}{_sentence(message)}"


def _field_diagnostic(field: object) -> str:
    return _attention_text(field)


def _query_diagnostic(query: object) -> str:
    return _attention_text(query)


def _console_findings(
    manifest: MigrationManifest,
) -> tuple[
    list[tuple[str, str, str, str]],
    list[tuple[str, str, str, str]],
    list[tuple[str, str, str, str]],
]:
    plan = manifest.generation
    blockers: list[tuple[str, str, str, str]] = []
    omissions: list[tuple[str, str, str, str]] = []
    attention: list[tuple[str, str, str, str]] = []

    fields_by_name = {field.source_name: field for field in manifest.fields}
    for omission in plan.omitted:
        field = fields_by_name.get(omission.source_path)
        path = _field_source_path(field) if field is not None else omission.source_path
        omissions.append((
            manifest.mapping_file,
            "FIELD",
            path,
            omission.reason,
        ))

    for reason in plan.package_blockers:
        blockers.append((manifest.mapping_file, "SETTING", "package", reason))

    analysis_fields = {
        str(field_name)
        for configuration in manifest.source_signals.get("text_analysis", {}).get("configurations", [])
        if configuration.get("status") != OperationalStatus.READY.value
        for field_name in configuration.get("fields", [])
    }
    for field in manifest.fields:
        status = _field_status(field)
        if status is OperationalStatus.READY_WITH_CAVEATS:
            if field.source_name in analysis_fields:
                continue
            attention.append((
                manifest.mapping_file,
                "FIELD",
                _field_source_path(field),
                _field_diagnostic(field),
            ))

    analysis_source = manifest.settings_file or manifest.mapping_file
    text_analysis = manifest.source_signals.get("text_analysis", {})
    for configuration in text_analysis.get("configurations", []):
        if configuration.get("status") == OperationalStatus.READY.value:
            continue
        kind = str(configuration.get("kind", "SETTING")).upper()
        attention.append((
            analysis_source,
            kind if kind in DIAGNOSTIC_KINDS else "SETTING",
            str(configuration.get("name", "unknown")),
            str(configuration.get("attention") or "Human review is required."),
        ))

    for query in manifest.queries:
        if query.decision is Decision.DIRECT:
            continue
        construct = query.source_constructs[0] if query.source_constructs else query.name
        attention.append((
            query.source_file,
            "QUERY",
            construct,
            _query_diagnostic(query),
        ))

    return blockers, omissions, attention


def _append_finding_section(
    lines: list[str],
    title: str,
    findings: list[tuple[str, str, str, str]],
) -> None:
    if not findings:
        return
    lines.extend(["", title])
    current_source: str | None = None
    for source, kind, path, message in findings:
        if source != current_source:
            lines.extend(["", source])
            current_source = source
        lines.append(_diagnostic_row(kind, path, message))


def render_console_summary(
    manifest: MigrationManifest,
    *,
    generation_status: str,
    manifest_path: str,
    vespa_app_path: str | None = None,
    next_step: str | None = None,
) -> str:
    """Render the complete human-facing result of the primary CLI flow."""
    coverage = manifest.coverage
    lines = [
        f"Project: {manifest.project_name}",
        f"Analysis: {manifest.validation.get('analysis', 'NOT_RUN')}",
        "",
        "Evidence",
        f"  {'Fields':<13}{coverage.fields_assessed}/{coverage.fields_supplied} assessed",
        (
            f"  {'Documents':<13}{coverage.documents_examined}/{coverage.documents_supplied} examined"
            if coverage.documents_supplied
            else f"  {'Documents':<13}not supplied"
        ),
        (
            f"  {'Queries':<13}{coverage.queries_assessed}/{coverage.queries_supplied} assessed"
            if coverage.queries_supplied
            else f"  {'Queries':<13}not supplied"
        ),
        "",
        "Schema",
    ]

    field_counts = {
        status: sum(_field_status(field) is status for field in manifest.fields)
        for status in OperationalStatus
    }
    for status in (
        OperationalStatus.READY,
        OperationalStatus.READY_WITH_CAVEATS,
        OperationalStatus.BLOCKED_DECISION,
        OperationalStatus.BLOCKED_REDESIGN,
    ):
        if field_counts[status]:
            lines.append(_console_count(CONSOLE_FIELD_STATUS_LABELS[status], field_counts[status]))

    text_analysis = manifest.source_signals.get("text_analysis", {})
    configurations = int(text_analysis.get("assessed", 0))
    if configurations:
        lines.extend(["", "Text analysis", _console_count("Configurations", configurations)])
        requires_attention = int(text_analysis.get("needs_attention", 0))
        if requires_attention:
            lines.append(_console_count("Requires attention", requires_attention))

    if manifest.queries:
        query_counts = {
            status: sum(_query_status(query) is status for query in manifest.queries)
            for status in OperationalStatus
        }
        lines.extend(["", "Queries"])
        for status in (
            OperationalStatus.READY,
            OperationalStatus.READY_WITH_CAVEATS,
            OperationalStatus.BLOCKED_DECISION,
            OperationalStatus.BLOCKED_REDESIGN,
        ):
            if query_counts[status]:
                lines.append(_console_count(CONSOLE_FIELD_STATUS_LABELS[status], query_counts[status]))

    blockers, omissions, attention = _console_findings(manifest)
    _append_finding_section(lines, "GENERATION BLOCKERS", blockers)
    _append_finding_section(lines, "OMITTED FROM GENERATED APP", omissions)
    _append_finding_section(lines, "NEEDS ATTENTION", attention)

    lines.append("")
    if generation_status == "BLOCKED":
        lines.append("Vespa app generation blocked.")
        lines.append(
            f"Next: Review the {len(blockers)} generation "
            f"{'blocker' if len(blockers) == 1 else 'blockers'} listed above."
        )
    elif generation_status in {"READY", "PARTIAL"}:
        lines.append(
            "Vespa app generated successfully."
            if generation_status == "READY"
            else "Vespa app generation: PARTIAL"
        )
        if generation_status == "PARTIAL":
            lines.append(
                f"Generated fields: {manifest.generation.generated_field_count}/"
                f"{manifest.generation.supplied_field_count}"
            )
        if vespa_app_path:
            lines.append(f"Vespa app: {vespa_app_path}")
        if next_step:
            lines.append(f"Next: {next_step}")
    elif generation_status == "ERROR":
        lines.append("Vespa app generation failed.")
        lines.append("Next: Rerun with --verbose for details.")
    else:
        lines.append("Vespa app generation not attempted.")
        if blockers:
            lines.append(
                f"Next: Review the {len(blockers)} generation "
                f"{'blocker' if len(blockers) == 1 else 'blockers'} listed above."
            )
        elif omissions:
            lines.append("Next: Review the omitted fields above, or rerun without --analyze-only.")
        else:
            lines.append("Next: Rerun without --analyze-only.")
    lines.extend(["", f"Manifest: {manifest_path}"])
    return "\n".join(lines)
