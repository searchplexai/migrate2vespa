from __future__ import annotations

from typing import Any

from ...manifest import FieldAssessment, OperationalStatus

# Built-in analyzers map to Vespa Lucene Linguistics; custom ones need review.
BUILTIN_LUCENE_ANALYZERS = {
    "simple", "whitespace", "stop", "keyword", "pattern", "fingerprint",
    "arabic", "armenian", "basque", "bengali", "brazilian", "bulgarian", "catalan",
    "cjk", "czech", "danish", "dutch", "english", "estonian", "finnish", "french",
    "galician", "german", "greek", "hindi", "hungarian", "indonesian", "irish",
    "italian", "latvian", "lithuanian", "norwegian", "persian", "portuguese",
    "romanian", "russian", "serbian", "sorani", "spanish", "swedish", "thai", "turkish",
}


def analysis_config(settings: Any) -> dict[str, Any]:
    """Return the effective index.analysis object from common settings exports."""
    current = settings.get("settings", settings) if isinstance(settings, dict) else {}
    if len(current) == 1 and isinstance(next(iter(current.values()), None), dict):
        candidate = next(iter(current.values()))
        if "settings" in candidate:
            current = candidate["settings"]
    index_settings = current.get("index", current) if isinstance(current, dict) else {}
    analysis = index_settings.get("analysis", {}) if isinstance(index_settings, dict) else {}
    return analysis if isinstance(analysis, dict) else {}


def _analyzer_assessment(
    name: str,
    analysis: dict[str, Any],
) -> tuple[bool, OperationalStatus, str | None, str, str]:
    definitions = analysis.get("analyzer") or {}
    explicitly_configured = isinstance(definitions, dict) and name in definitions

    if name == "standard" and not explicitly_configured:
        return True, OperationalStatus.READY, None, "VESPA_DEFAULT", "ES-FIELD-TEXT-001"
    if name in BUILTIN_LUCENE_ANALYZERS and not explicitly_configured:
        return (
            True,
            OperationalStatus.READY_WITH_CAVEATS,
            "Use Vespa Lucene Linguistics.",
            "LUCENE_LINGUISTICS",
            "ES-LUCENE-ANALYZER-001",
        )
    if explicitly_configured:
        return (
            False,
            OperationalStatus.BLOCKED_DECISION,
            "Custom analyzer requires human review.",
            "UNRESOLVED",
            "ES-CUSTOM-ANALYZER-001",
        )
    return (
        False,
        OperationalStatus.BLOCKED_DECISION,
        "Unrecognized analyzer requires human review.",
        "UNRESOLVED",
        "ES-CUSTOM-ANALYZER-001",
    )


def _normalizer_assessment(
    name: str,
    analysis: dict[str, Any],
) -> tuple[bool, OperationalStatus, str | None, str, str]:
    definitions = analysis.get("normalizer") or {}
    definition = definitions.get(name) if isinstance(definitions, dict) else None

    lowercase_only = (
        name == "lowercase"
        or (
            isinstance(definition, dict)
            and definition.get("type", "custom") == "custom"
            and not (definition.get("char_filter") or [])
            and (definition.get("filter") or []) == ["lowercase"]
        )
    )
    if lowercase_only:
        return (
            True,
            OperationalStatus.READY_WITH_CAVEATS,
            "Lowercase normalization is generated.",
            "INGESTION_TRANSFORM",
            "ES-KEYWORD-NORMALIZER-001",
        )
    return (
        False,
        OperationalStatus.BLOCKED_DECISION,
        "Custom or unrecognized normalizer requires human review.",
        "UNRESOLVED",
        "ES-KEYWORD-NORMALIZER-001",
    )


def assess_text_analysis(fields: list[FieldAssessment], settings: Any) -> dict[str, Any]:
    """Classify every referenced analyzer and normalizer."""
    analysis = analysis_config(settings)
    references: dict[tuple[str, str], set[str]] = {}
    for field in fields:
        if field.source_type in {"text", "match_only_text"}:
            index_analyzer = str(field.source_properties.get("analyzer", "standard"))
            references.setdefault(("analyzer", index_analyzer), set()).add(field.source_name)
            search_analyzer = field.source_properties.get("search_analyzer")
            if search_analyzer is not None:
                references.setdefault(("analyzer", str(search_analyzer)), set()).add(field.source_name)
        normalizer = field.source_properties.get("normalizer")
        if normalizer is not None:
            references.setdefault(("normalizer", str(normalizer)), set()).add(field.source_name)

    configurations: list[dict[str, Any]] = []
    for (kind, name), field_names in sorted(references.items()):
        recognized, status, attention, path, rule = (
            _analyzer_assessment(name, analysis)
            if kind == "analyzer"
            else _normalizer_assessment(name, analysis)
        )
        configurations.append({
            "name": name,
            "kind": kind,
            "fields": sorted(field_names),
            "recognized": recognized,
            "status": status.value,
            "attention": attention,
            "vespa_paths": [path],
            "rule": rule,
        })

    return {
        "text_analysis": {
            "assessed": len(configurations),
            "recognized": sum(item["recognized"] for item in configurations),
            "unrecognized": sum(not item["recognized"] for item in configurations),
            "needs_attention": sum(
                item["status"] != OperationalStatus.READY.value
                for item in configurations
            ),
            "configurations": configurations,
        }
    }
