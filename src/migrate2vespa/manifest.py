from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Mapping


class Decision(str, Enum):
    DIRECT = "DIRECT"
    ADAPT = "ADAPT"
    REVIEW = "REVIEW"
    REDESIGN = "REDESIGN"


class OperationalStatus(str, Enum):
    READY = "READY"
    READY_WITH_CAVEATS = "READY — WITH CAVEATS"
    BLOCKED_DECISION = "BLOCKED — DECISION REQUIRED"
    BLOCKED_REDESIGN = "BLOCKED — REDESIGN REQUIRED"


class GenerationScope(str, Enum):
    FIELD = "FIELD"
    SUBTREE = "SUBTREE"
    PACKAGE = "PACKAGE"


class EvidenceLevel(str, Enum):
    DECLARED = "DECLARED"
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    VALIDATED = "VALIDATED"


class CardinalityState(str, Enum):
    NOT_OBSERVED = "NOT_OBSERVED"
    SCALAR_ONLY_OBSERVED = "SCALAR_ONLY_OBSERVED"
    MULTI_OBSERVED = "MULTI_OBSERVED"
    MIXED_OBSERVED = "MIXED_OBSERVED"


class RequiredCapability(str, Enum):
    TEXT_MATCH = "TEXT_MATCH"
    EXACT_MATCH = "EXACT_MATCH"
    FILTER = "FILTER"
    RANGE = "RANGE"
    SORT = "SORT"
    GROUP = "GROUP"
    ANN = "ANN"


class LogicalType(str, Enum):
    """Source-independent field meaning used by the shared Vespa planner."""

    TEXT = "TEXT"
    STRING = "STRING"
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    LONG = "LONG"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    TEMPORAL = "TEMPORAL"
    VECTOR = "VECTOR"


JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
FORMAT_VERSION = 1


def value_at_path(value: Any, path: str) -> Any:
    """Read a dotted manifest source path from an object or object array."""
    parts = path.split(".") if path else []
    if not parts or value is None:
        return value
    if isinstance(value, list):
        values = [value_at_path(item, path) for item in value]
        return [item for item in values if item is not None]
    if not isinstance(value, dict):
        return None
    return value_at_path(value.get(parts[0]), ".".join(parts[1:]))


@dataclass(frozen=True)
class TransformSpec:
    operation: str
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "parameters": dict(sorted(self.parameters.items())),
        }

    @classmethod
    def from_value(cls, value: Any) -> "TransformSpec":
        if not isinstance(value, dict) or not isinstance(value.get("operation"), str):
            raise ValueError(f"Invalid transform specification: {value!r}")
        parameters = value.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(f"Transform parameters must be an object: {value!r}")
        return cls(operation=value["operation"], parameters=dict(parameters))


@dataclass(frozen=True)
class AnnPlan:
    distance_metric: str
    hnsw_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "hnsw_enabled": self.hnsw_enabled,
            "distance_metric": self.distance_metric,
        }

    @classmethod
    def from_value(cls, value: Any) -> "AnnPlan | None":
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"Invalid ANN plan: {value!r}")
        metric = value.get("distance_metric")
        if not isinstance(metric, str) or not metric:
            raise ValueError(f"Invalid ANN distance metric: {value!r}")
        if not isinstance(value.get("hnsw_enabled"), bool):
            raise ValueError(f"Invalid ANN HNSW flag: {value!r}")
        return cls(distance_metric=metric, hnsw_enabled=value["hnsw_enabled"])


@dataclass(frozen=True)
class MatchPlan:
    """Structured Vespa match semantics.

    Mode and case are separate schema properties. Keeping them separate avoids
    serializing combinations such as ``word + cased`` as an invented value.
    """

    mode: str
    case: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "case": self.case}

    def pyvespa_properties(self) -> list[str]:
        return [item for item in (self.mode, self.case) if item]

    @classmethod
    def from_value(cls, value: Any) -> "MatchPlan | None":
        if value is None:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("mode"), str):
            raise ValueError(f"Invalid match plan: {value!r}")
        case = value.get("case")
        if case is not None and not isinstance(case, str):
            raise ValueError(f"Invalid match case: {value!r}")
        return cls(value["mode"], case)


@dataclass(frozen=True)
class TargetFieldPlan:
    name: str
    source_path: str
    type: str
    indexing: tuple[str, ...]
    match: MatchPlan | None = None
    rank: str | None = None
    bm25: bool = False
    ann: AnnPlan | None = None
    transforms: tuple[TransformSpec, ...] = ()
    generation_note: str | None = None

    def to_dict(self, *, artifact: str | None = None) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "type": self.type,
            "indexing": list(self.indexing),
            "match": self.match.to_dict() if self.match else None,
            "rank": self.rank,
            "bm25": self.bm25,
            "ann": self.ann.to_dict() if self.ann else None,
            "transforms": [item.to_dict() for item in self.transforms],
            "generation_note": self.generation_note,
            "artifact": artifact,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TargetFieldPlan | None":
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"Invalid target field plan: {value!r}")
        required = {"name", "source_path", "type", "indexing", "bm25", "transforms"}
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(f"Target field plan is missing: {', '.join(missing)}")
        if not all(isinstance(value[key], str) for key in ("name", "source_path", "type")):
            raise ValueError(f"Invalid target field identity or type: {value!r}")
        if not isinstance(value["indexing"], list) or not all(
            isinstance(item, str) for item in value["indexing"]
        ):
            raise ValueError(f"Invalid target indexing: {value!r}")
        if not isinstance(value["bm25"], bool):
            raise ValueError(f"Invalid target BM25 flag: {value!r}")
        if not isinstance(value["transforms"], list):
            raise ValueError(f"Invalid target transforms: {value!r}")
        match = MatchPlan.from_value(value.get("match"))
        transforms = tuple(TransformSpec.from_value(item) for item in value["transforms"])
        ann = AnnPlan.from_value(value.get("ann"))
        return cls(
            name=value["name"],
            source_path=value["source_path"],
            type=value["type"],
            indexing=tuple(value["indexing"]),
            match=match,
            rank=str(value["rank"]) if value.get("rank") is not None else None,
            bm25=value["bm25"],
            ann=ann,
            transforms=transforms,
            generation_note=(str(value["generation_note"]) if value.get("generation_note") is not None else None),
        )


@dataclass(frozen=True)
class Claim:
    value: Any
    evidence_level: EvidenceLevel
    source: str
    rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "value": self.value,
            "evidence_level": self.evidence_level.value,
            "source": self.source,
        }
        if self.rule is not None:
            value["rule"] = self.rule
        return value

    @classmethod
    def from_value(
        cls,
        value: Any,
    ) -> "Claim":
        if not isinstance(value, dict) or "value" not in value:
            raise ValueError(f"Invalid manifest claim: {value!r}")
        return cls(
            value=value.get("value"),
            evidence_level=EvidenceLevel(str(value["evidence_level"])),
            source=str(value["source"]),
            rule=str(value["rule"]) if value.get("rule") is not None else None,
        )


@dataclass
class CardinalityObservation:
    state: CardinalityState = CardinalityState.NOT_OBSERVED
    documents_examined: int = 0
    scalar_values_seen: int = 0
    array_values_seen: int = 0
    null_or_missing: int = 0
    evidence_level: EvidenceLevel | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "evidence_level": self.evidence_level.value if self.evidence_level else None,
            "source": self.source,
            "documents_examined": self.documents_examined,
            "scalar_values_seen": self.scalar_values_seen,
            "array_values_seen": self.array_values_seen,
            "null_or_missing": self.null_or_missing,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CardinalityObservation":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid cardinality observation: {value!r}")
        documents_examined = _nonnegative_int(
            value.get("documents_examined", 0), "cardinality.documents_examined"
        )
        scalar_values_seen = _nonnegative_int(
            value.get("scalar_values_seen", 0), "cardinality.scalar_values_seen"
        )
        array_values_seen = _nonnegative_int(
            value.get("array_values_seen", 0), "cardinality.array_values_seen"
        )
        null_or_missing = _nonnegative_int(
            value.get("null_or_missing", 0), "cardinality.null_or_missing"
        )
        if scalar_values_seen + array_values_seen + null_or_missing != documents_examined:
            raise ValueError("Cardinality counts must add up to documents_examined")
        return cls(
            state=CardinalityState(str(value["state"])),
            documents_examined=documents_examined,
            scalar_values_seen=scalar_values_seen,
            array_values_seen=array_values_seen,
            null_or_missing=null_or_missing,
            evidence_level=(
                EvidenceLevel(str(value["evidence_level"]))
                if value.get("evidence_level") is not None
                else None
            ),
            source=str(value["source"]) if value.get("source") is not None else None,
        )


@dataclass
class CoverageLedger:
    fields_supplied: int = 0
    fields_assessed: int = 0
    fields_unassessed: int = 0
    queries_supplied: int = 0
    queries_assessed: int = 0
    queries_invalid: int = 0
    queries_unassessed: int = 0
    documents_supplied: int = 0
    documents_examined: int = 0
    documents_invalid: int = 0

    @property
    def all_supplied_accounted_for(self) -> bool:
        """Return whether every supplied artifact has an explicit outcome."""
        return (
            self.fields_unassessed == 0
            and self.fields_assessed == self.fields_supplied
            and self.queries_unassessed == 0
            and self.queries_assessed + self.queries_invalid == self.queries_supplied
            and self.documents_examined + self.documents_invalid == self.documents_supplied
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {
                "supplied": self.fields_supplied,
                "assessed": self.fields_assessed,
                "unassessed": self.fields_unassessed,
            },
            "queries": {
                "supplied": self.queries_supplied,
                "assessed": self.queries_assessed,
                "invalid": self.queries_invalid,
                "unassessed": self.queries_unassessed,
            },
            "documents": {
                "supplied": self.documents_supplied,
                "examined": self.documents_examined,
                "invalid": self.documents_invalid,
            },
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CoverageLedger":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid coverage ledger: {value!r}")
        field_value = _mapping(value.get("fields"), "coverage.fields")
        query_value = _mapping(value.get("queries"), "coverage.queries")
        document_value = _mapping(value.get("documents"), "coverage.documents")
        return cls(
            fields_supplied=_nonnegative_int(
                field_value.get("supplied", 0), "coverage.fields.supplied"
            ),
            fields_assessed=_nonnegative_int(
                field_value.get("assessed", 0), "coverage.fields.assessed"
            ),
            fields_unassessed=_nonnegative_int(
                field_value.get("unassessed", 0), "coverage.fields.unassessed"
            ),
            queries_supplied=_nonnegative_int(
                query_value.get("supplied", 0), "coverage.queries.supplied"
            ),
            queries_assessed=_nonnegative_int(
                query_value.get("assessed", 0), "coverage.queries.assessed"
            ),
            queries_invalid=_nonnegative_int(
                query_value.get("invalid", 0), "coverage.queries.invalid"
            ),
            queries_unassessed=_nonnegative_int(
                query_value.get("unassessed", 0), "coverage.queries.unassessed"
            ),
            documents_supplied=_nonnegative_int(
                document_value.get("supplied", 0), "coverage.documents.supplied"
            ),
            documents_examined=_nonnegative_int(
                document_value.get("examined", 0), "coverage.documents.examined"
            ),
            documents_invalid=_nonnegative_int(
                document_value.get("invalid", 0), "coverage.documents.invalid"
            ),
        )


DECISION_WEIGHT = {
    Decision.DIRECT: 0,
    Decision.ADAPT: 1,
    Decision.REVIEW: 2,
    Decision.REDESIGN: 3,
}


def worst_decision(*decisions: Decision) -> Decision:
    return max(decisions, key=DECISION_WEIGHT.__getitem__, default=Decision.DIRECT)


@dataclass
class FieldAssessment:
    source_name: str
    source_type: str
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    source_properties: dict[str, Any] = field(default_factory=dict)
    observed_usage: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)
    support: "Support" = field(default_factory=lambda: Support())
    returned_in_documents: bool = False
    risks: list[str] = field(default_factory=list)
    generation_note: str | None = None
    source_type_claim: Claim | None = None
    effective_semantics: dict[str, Claim] = field(default_factory=dict)
    cardinality_observation: CardinalityObservation = field(default_factory=CardinalityObservation)
    usage_sources: dict[str, list[str]] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    confidence: dict[str, str] = field(default_factory=dict)
    distance_metric: str | None = None
    required_capabilities: list[RequiredCapability] = field(default_factory=list)
    target_plan: TargetFieldPlan | None = None
    generation_scope: GenerationScope = GenerationScope.FIELD
    logical_type: LogicalType | None = None
    source_value_path: str | None = None
    value_transforms: list[TransformSpec] = field(default_factory=list)
    vector_dimensions: int | None = None
    vector_cell_type: str | None = None


@dataclass
class QueryAssessment:
    name: str
    source_file: str
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    source_constructs: list[str] = field(default_factory=list)
    observed_fields: list[str] = field(default_factory=list)
    field_usage: dict[str, list[str]] = field(default_factory=dict)
    migration_signals: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    source_evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    sort_evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Support:
    detect: bool = True
    generate: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {"detect": self.detect, "generate": self.generate}

    @classmethod
    def from_value(cls, value: Any) -> "Support":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid support declaration: {value!r}")
        detect = value.get("detect", True)
        generate = value.get("generate", True)
        if not isinstance(detect, bool) or not isinstance(generate, bool):
            raise ValueError(f"Support flags must be booleans: {value!r}")
        return cls(detect, generate)


@dataclass(frozen=True)
class GenerationOmission:
    source_path: str
    scope: GenerationScope
    decision: Decision
    rules: tuple[str, ...]
    reason: str
    covers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "kind": "FIELD",
            "scope": self.scope.value,
            "decision": self.decision.value,
            "rules": list(self.rules),
            "reason": self.reason,
            "covers": list(self.covers),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GenerationOmission":
        return cls(
            source_path=str(value.get("source_path", "")),
            scope=GenerationScope(str(value.get("scope", GenerationScope.FIELD.value))),
            decision=Decision(str(value.get("decision", Decision.REVIEW.value))),
            rules=tuple(str(item) for item in value.get("rules", [])),
            reason=str(value.get("reason", "Human migration decision required")),
            covers=tuple(str(item) for item in value.get("covers", [])),
        )


@dataclass
class GenerationPlan:
    status: str = "NOT_RUN"
    planned_outcome: str = "READY"
    generated_field_count: int = 0
    supplied_field_count: int = 0
    omitted: list[GenerationOmission] = field(default_factory=list)
    package_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "planned_outcome": self.planned_outcome,
            "generated_field_count": self.generated_field_count,
            "supplied_field_count": self.supplied_field_count,
            "omitted": [item.to_dict() for item in self.omitted],
            "package_blockers": list(self.package_blockers),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "GenerationPlan":
        if not isinstance(value, dict):
            raise ValueError(f"Invalid generation plan: {value!r}")
        status = value.get("status", "NOT_RUN")
        planned_outcome = value.get("planned_outcome", "READY")
        allowed_statuses = {"NOT_RUN", "READY", "PARTIAL", "BLOCKED", "ERROR"}
        if status not in allowed_statuses or planned_outcome not in {"READY", "PARTIAL", "BLOCKED"}:
            raise ValueError(f"Invalid generation status: {value!r}")
        return cls(
            status=status,
            planned_outcome=planned_outcome,
            generated_field_count=_nonnegative_int(
                value.get("generated_field_count", 0), "generation.generated_field_count"
            ),
            supplied_field_count=_nonnegative_int(
                value.get("supplied_field_count", 0), "generation.supplied_field_count"
            ),
            omitted=[GenerationOmission.from_dict(item) for item in value.get("omitted", [])],
            package_blockers=[str(item) for item in value.get("package_blockers", [])],
        )


@dataclass
class MigrationManifest:
    tool_version: str
    project_name: str
    input_directory: str
    fields: list[FieldAssessment]
    queries: list[QueryAssessment]
    format_version: ClassVar[int] = FORMAT_VERSION
    settings: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document_count: int = 0
    document_digest: str | None = None
    synthetic_document_id_count: int = 0
    settings_present: bool = False
    mapping_file: str = "mapping.json"
    settings_file: str | None = None
    field_aliases: dict[str, str] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    configured_input_directory: str | None = field(default=None, repr=False)
    source_engine: str = "unknown"
    source_version: str | None = None
    source_id: str = "elastic-family"
    source_index_name: str | None = None
    source_signals: dict[str, Any] = field(default_factory=dict)
    coverage: CoverageLedger = field(default_factory=CoverageLedger)
    generation: GenerationPlan = field(default_factory=GenerationPlan)

    @property
    def unresolved(self) -> list[str]:
        return list(dict.fromkeys(self.blockers + self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "tool": {"name": "migrate2vespa", "version": self.tool_version},
            "source": {
                "id": self.source_id,
                "engine": self.source_engine,
                "version": self.source_version,
                "index": self.source_index_name,
                "input_directory": self.configured_input_directory or self.input_directory,
            },
            "target": {
                "platform": "vespa",
                "schema": self.project_name,
            },
            "evidence": {
                "mapping": self.mapping_file,
                "settings": self.settings_file if self.settings_present else None,
                "query_count": len(self.queries),
                "document_count": self.document_count,
                "documents_sha256": self.document_digest,
                "synthetic_document_id_count": self.synthetic_document_id_count,
            },
            "coverage": self.coverage.to_dict(),
            "summary": self._summary(),
            "validation": self.validation or {
                "analysis": (
                    "COMPLETE"
                    if self.coverage.all_supplied_accounted_for
                    else "INCOMPLETE"
                ),
                "generation": "NOT_RUN",
            },
            "generation": self.generation.to_dict(),
            "field_aliases": self.field_aliases,
            "fields": {
                item.source_name: {
                    "source": {
                        "path": item.source_name,
                        "declared": _declared_source(item),
                        "effective": _effective_source(item),
                    },
                    "evidence": {
                        "documents": {
                            "cardinality": item.cardinality_observation.to_dict(),
                        },
                        "queries": {
                            usage: {
                                "query_file_count": len(sources),
                                "evidence_level": EvidenceLevel.OBSERVED.value,
                                "sources": sources,
                            }
                            for usage, sources in sorted(item.usage_sources.items())
                        },
                    },
                    "inference": {
                        "rules": item.rules,
                        "reasons": item.reasons,
                    },
                    "required": {
                        "capabilities": [capability.value for capability in item.required_capabilities],
                    },
                    "normalized": {
                        "logical_type": item.logical_type.value if item.logical_type else None,
                        "source_value_path": item.source_value_path or item.source_name,
                        "value_transforms": [transform.to_dict() for transform in item.value_transforms],
                        "vector": (
                            {
                                "dimensions": item.vector_dimensions,
                                "cell_type": item.vector_cell_type,
                                "distance_metric": item.distance_metric,
                            }
                            if item.logical_type is LogicalType.VECTOR
                            else None
                        ),
                    },
                    "decision": item.decision.value,
                    "support": item.support.to_dict(),
                    "generation": {"scope": item.generation_scope.value},
                    "confidence": item.confidence,
                    "risks": item.risks,
                    "assumptions": item.assumptions,
                    "unresolved": item.unresolved,
                    "target": _serialize_target(item, self.project_name),
                }
                for item in self.fields
            },
            "queries": {
                item.name: {
                    "source": {
                        "file": item.source_file,
                        "constructs": item.source_constructs,
                        "evidence_level": EvidenceLevel.DECLARED.value,
                    },
                    "evidence": {
                        "fields": item.observed_fields,
                        "field_usage": item.field_usage,
                        "sort": item.sort_evidence,
                    },
                    "inference": {
                        "rules": item.rules,
                        "reasons": item.reasons,
                    },
                    "migration_signals": item.migration_signals,
                    "decision": item.decision.value,
                    "risks": item.risks,
                }
                for item in self.queries
            },
            "source_signals": self.source_signals,
            "settings": self.settings,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    def _summary(self) -> dict[str, dict[str, int]]:
        def counts(items: list[Any]) -> dict[str, int]:
            result = {decision.value.lower(): 0 for decision in Decision}
            for item in items:
                result[item.decision.value.lower()] += 1
            return result

        return {"fields": counts(self.fields), "queries": counts(self.queries)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MigrationManifest":
        if not isinstance(value, dict):
            raise ValueError("Migration manifest must be an object")
        format_version = value.get("format_version")
        if (
            isinstance(format_version, bool)
            or not isinstance(format_version, int)
            or format_version != FORMAT_VERSION
        ):
            raise ValueError(
                f"Unsupported migration manifest format_version: {format_version}; "
                f"this release accepts format_version {FORMAT_VERSION}"
            )

        tool = _mapping(value.get("tool"), "tool")
        source = _mapping(value.get("source"), "source")
        target = _mapping(value.get("target"), "target")
        evidence = _mapping(value.get("evidence"), "evidence")
        raw_fields = _mapping(value.get("fields"), "fields")
        raw_queries = _mapping(value.get("queries"), "queries")
        if tool.get("name") != "migrate2vespa":
            raise ValueError(f"Unsupported manifest tool: {tool.get('name')!r}")
        if target.get("platform") != "vespa":
            raise ValueError(f"Unsupported target platform: {target.get('platform')!r}")

        fields = [
            _field_from_dict(field_name, _mapping(item, f"fields.{field_name}"))
            for field_name, item in raw_fields.items()
        ]
        queries = [
            _query_from_dict(query_name, _mapping(item, f"queries.{query_name}"))
            for query_name, item in raw_queries.items()
        ]
        settings_evidence = evidence.get("settings")
        source_index = source.get("index")
        source_version = source.get("version")
        coverage = CoverageLedger.from_dict(value.get("coverage"))
        document_count = _nonnegative_int(
            evidence.get("document_count", 0), "evidence.document_count"
        )
        synthetic_document_id_count = _nonnegative_int(
            evidence.get("synthetic_document_id_count", 0),
            "evidence.synthetic_document_id_count",
        )
        query_count = _nonnegative_int(
            evidence.get("query_count", 0), "evidence.query_count"
        )
        _validate_manifest_counts(
            coverage,
            field_count=len(fields),
            query_count=len(queries),
            evidence_query_count=query_count,
            document_count=document_count,
            synthetic_document_id_count=synthetic_document_id_count,
        )
        document_digest = (
            str(evidence["documents_sha256"])
            if evidence.get("documents_sha256") is not None
            else None
        )
        if document_count and document_digest is None:
            raise ValueError(
                "Manifest evidence.documents_sha256 is required when documents were examined"
            )
        return cls(
            tool_version=str(tool["version"]),
            project_name=str(target["schema"]),
            input_directory=str(source["input_directory"]),
            fields=fields,
            queries=queries,
            settings=dict(_mapping(value.get("settings", {}), "settings")),
            blockers=[str(item) for item in value.get("blockers", [])],
            warnings=[str(item) for item in value.get("warnings", [])],
            document_count=document_count,
            document_digest=document_digest,
            synthetic_document_id_count=synthetic_document_id_count,
            settings_present=settings_evidence is not None,
            mapping_file=str(evidence.get("mapping", "mapping.json")),
            settings_file=str(settings_evidence) if settings_evidence is not None else None,
            field_aliases={str(key): str(item) for key, item in _mapping(value.get("field_aliases", {}), "field_aliases").items()},
            validation=dict(_mapping(value.get("validation", {}), "validation")),
            source_engine=str(source.get("engine", "unknown")),
            source_version=str(source_version) if source_version is not None else None,
            source_id=str(source["id"]),
            source_index_name=str(source_index) if source_index is not None else None,
            source_signals=dict(_mapping(value.get("source_signals", {}), "source_signals")),
            coverage=coverage,
            generation=GenerationPlan.from_dict(value.get("generation")),
        )

    @property
    def input_path(self) -> Path:
        return Path(self.input_directory)

def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Manifest {path} must be an object")
    return value


def _nonnegative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Manifest {path} must be a non-negative integer")
    return value


def _positive_int(value: Any, path: str) -> int:
    result = _nonnegative_int(value, path)
    if result == 0:
        raise ValueError(f"Manifest {path} must be a positive integer")
    return result


def _validate_manifest_counts(
    coverage: CoverageLedger,
    *,
    field_count: int,
    query_count: int,
    evidence_query_count: int,
    document_count: int,
    synthetic_document_id_count: int,
) -> None:
    if (
        coverage.fields_assessed + coverage.fields_unassessed
        != coverage.fields_supplied
    ):
        raise ValueError("Field coverage counts are inconsistent")
    if (
        coverage.queries_assessed
        + coverage.queries_invalid
        + coverage.queries_unassessed
        != coverage.queries_supplied
    ):
        raise ValueError("Query coverage counts are inconsistent")
    if (
        coverage.documents_examined + coverage.documents_invalid
        != coverage.documents_supplied
    ):
        raise ValueError("Document coverage counts are inconsistent")
    if field_count != coverage.fields_assessed:
        raise ValueError("Manifest field count does not match assessed field coverage")
    if query_count != coverage.queries_assessed + coverage.queries_invalid:
        raise ValueError("Manifest query count does not match assessed query coverage")
    if evidence_query_count != query_count:
        raise ValueError("Manifest evidence.query_count does not match query assessments")
    if document_count != coverage.documents_examined:
        raise ValueError("Manifest evidence.document_count does not match examined documents")
    if synthetic_document_id_count > document_count:
        raise ValueError("Synthetic document ID count exceeds examined documents")


def _field_from_dict(field_name: str, item: dict[str, Any]) -> FieldAssessment:
    source = _mapping(item.get("source"), f"fields.{field_name}.source")
    declared_raw = _mapping(source.get("declared", {}), f"fields.{field_name}.source.declared")
    effective_raw = _mapping(source.get("effective", {}), f"fields.{field_name}.source.effective")
    raw_type = declared_raw.get("type", effective_raw.get("type"))
    if raw_type is None:
        raise ValueError(f"Manifest field {field_name} has no source type claim")
    source_type_claim = Claim.from_value(raw_type)
    declared = dict(declared_raw)
    if "type" in declared:
        declared["type"] = source_type_claim.value
    effective = {
        name: Claim.from_value(claim)
        for name, claim in effective_raw.items()
        if name != "type"
    }

    evidence = _mapping(item.get("evidence"), f"fields.{field_name}.evidence")
    document_evidence = _mapping(evidence.get("documents"), f"fields.{field_name}.evidence.documents")
    cardinality = CardinalityObservation.from_dict(document_evidence.get("cardinality"))
    query_evidence = _mapping(evidence.get("queries", {}), f"fields.{field_name}.evidence.queries")
    usage_sources = {
        str(usage): [str(path) for path in _mapping(claim, f"fields.{field_name}.evidence.queries.{usage}").get("sources", [])]
        for usage, claim in query_evidence.items()
    }

    inference = _mapping(item.get("inference"), f"fields.{field_name}.inference")
    required = _mapping(item.get("required"), f"fields.{field_name}.required")
    normalized = _mapping(item.get("normalized"), f"fields.{field_name}.normalized")
    generation = _mapping(item.get("generation"), f"fields.{field_name}.generation")
    raw_target = item.get("target")
    vector = normalized.get("vector") or {}
    if not isinstance(vector, dict):
        raise ValueError(f"Manifest fields.{field_name}.normalized.vector must be an object or null")
    source_path = str(source.get("path", field_name))
    target_plan = TargetFieldPlan.from_dict(raw_target)
    return FieldAssessment(
        source_name=source_path,
        source_type=str(source_type_claim.value),
        decision=Decision(str(item["decision"])),
        reasons=[str(value) for value in inference.get("reasons", [])],
        source_properties=declared,
        observed_usage=sorted(usage_sources),
        rules=[str(value) for value in inference.get("rules", [])],
        source_evidence=[source_type_claim.source],
        support=Support.from_value(item.get("support")),
        returned_in_documents=(cardinality.scalar_values_seen + cardinality.array_values_seen > 0),
        risks=[str(value) for value in item.get("risks", [])],
        generation_note=(target_plan.generation_note if target_plan else None),
        source_type_claim=source_type_claim,
        effective_semantics=effective,
        cardinality_observation=cardinality,
        usage_sources=usage_sources,
        assumptions=[str(value) for value in item.get("assumptions", [])],
        unresolved=[str(value) for value in item.get("unresolved", [])],
        confidence={str(key): str(value) for key, value in _mapping(item.get("confidence", {}), f"fields.{field_name}.confidence").items()},
        distance_metric=(str(vector["distance_metric"]) if vector.get("distance_metric") is not None else None),
        required_capabilities=[RequiredCapability(str(value)) for value in required.get("capabilities", [])],
        target_plan=target_plan,
        generation_scope=GenerationScope(str(generation.get("scope", GenerationScope.FIELD.value))),
        logical_type=(LogicalType(str(normalized["logical_type"])) if normalized.get("logical_type") is not None else None),
        source_value_path=(str(normalized["source_value_path"]) if normalized.get("source_value_path") is not None else None),
        value_transforms=[TransformSpec.from_value(value) for value in normalized.get("value_transforms", [])],
        vector_dimensions=(
            _positive_int(
                vector["dimensions"],
                f"fields.{field_name}.normalized.vector.dimensions",
            )
            if vector.get("dimensions") is not None
            else None
        ),
        vector_cell_type=(str(vector["cell_type"]) if vector.get("cell_type") is not None else None),
    )


def _query_from_dict(query_name: str, item: dict[str, Any]) -> QueryAssessment:
    source = _mapping(item.get("source"), f"queries.{query_name}.source")
    evidence = _mapping(item.get("evidence"), f"queries.{query_name}.evidence")
    inference = _mapping(item.get("inference"), f"queries.{query_name}.inference")
    source_file = str(source["file"])
    return QueryAssessment(
        name=query_name,
        source_file=source_file,
        decision=Decision(str(item["decision"])),
        reasons=[str(value) for value in inference.get("reasons", [])],
        source_constructs=[str(value) for value in source.get("constructs", [])],
        observed_fields=[str(value) for value in evidence.get("fields", [])],
        field_usage={str(key): [str(value) for value in values] for key, values in _mapping(evidence.get("field_usage", {}), f"queries.{query_name}.evidence.field_usage").items()},
        migration_signals=[str(value) for value in item.get("migration_signals", [])],
        rules=[str(value) for value in inference.get("rules", [])],
        source_evidence=[source_file],
        risks=[str(value) for value in item.get("risks", [])],
        sort_evidence=list(evidence.get("sort", [])),
    )

def _serialize_target(item: FieldAssessment, project_name: str) -> dict[str, Any] | None:
    if item.target_plan is not None:
        return item.target_plan.to_dict(
            artifact=(f"vespa-app/schemas/{project_name}.sd" if item.support.generate else None)
        )
    return None



def _source_type_claim(item: FieldAssessment) -> Claim:
    return item.source_type_claim or Claim(
        value=item.source_type,
        evidence_level=(EvidenceLevel.DECLARED if "type" in item.source_properties else EvidenceLevel.INFERRED),
        source=f"mapping.properties.{item.source_name}",
    )


def _declared_source(item: FieldAssessment) -> dict[str, Any]:
    declared = dict(item.source_properties)
    claim = _source_type_claim(item)
    if claim.evidence_level is EvidenceLevel.DECLARED:
        declared["type"] = claim.to_dict()
    else:
        declared.pop("type", None)
    return declared


def _effective_source(item: FieldAssessment) -> dict[str, Any]:
    effective = {name: claim.to_dict() for name, claim in item.effective_semantics.items()}
    claim = _source_type_claim(item)
    if claim.evidence_level is not EvidenceLevel.DECLARED:
        effective["type"] = claim.to_dict()
    return effective
