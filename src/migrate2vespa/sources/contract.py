from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..manifest import FieldAssessment, QueryAssessment
from .rules import RuleRegistry


@dataclass(frozen=True)
class DecodedSourceDocument:
    source_id: str
    fields: dict[str, Any]
    source_path: str
    sequence: int
    synthetic_id: bool = False


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    source_path: str
    supplied_count: int
    parsed_count: int
    invalid_count: int


@dataclass(frozen=True)
class ParsedQueryArtifact:
    query_id: str
    source_file: str
    body: dict[str, Any] | None
    error: str | None = None


@dataclass
class SourceInspection:
    source_id: str
    index_name: str
    source_index_name: str | None
    mapping: dict[str, Any]
    settings: Any
    settings_present: bool
    field_declarations: list[tuple[str, dict[str, Any], str | None]]
    documents: list[DecodedSourceDocument]
    queries: list[ParsedQueryArtifact]
    artifacts: list[ArtifactRecord]
    document_issues: list[str]
    document_digest: str | None = None
    mapping_file: str = "mapping.json"
    settings_file: str | None = None


class DocumentDecoder(Protocol):
    """Minimal source interface needed while rendering an optional feed."""

    def decode_document(
        self,
        raw: dict[str, Any],
        sequence: int,
        source_path: str,
    ) -> DecodedSourceDocument: ...


@dataclass
class SourceAssessment:
    """Source-interpreted facts consumed by shared reconciliation."""

    fields: list[FieldAssessment]
    queries: list[QueryAssessment]
    settings_summary: dict[str, Any]
    source_signals: dict[str, Any]
    mapping_issues: list[str]
    settings_issues: list[str]
    document_issues: list[str]


class Source(DocumentDecoder, Protocol):
    """Contract implemented by one source family."""

    id: str
    registry: RuleRegistry

    def inspect(self, input_directory: Path) -> SourceInspection: ...

    def assess(self, inspection: SourceInspection) -> SourceAssessment: ...
