from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...io import (
    InputError,
    file_sha256,
    inspect_jsonl,
    iter_json_files,
    read_json,
    resolve_input_json,
)
from ..contract import (
    ArtifactRecord,
    DecodedSourceDocument,
    ParsedQueryArtifact,
    SourceInspection,
)


def inspect(input_directory: Path, *, source_id: str) -> SourceInspection:
    mapping_path = resolve_input_json(
        input_directory,
        exact_name="mapping.json",
        name_token="mapping",
        required=True,
        kind="mapping",
    )
    assert mapping_path is not None
    mapping = read_json(mapping_path)
    if not isinstance(mapping, dict):
        raise InputError(f"{mapping_path.name} must contain a JSON object")
    settings_path = resolve_input_json(
        input_directory,
        exact_name="settings.json",
        name_token="setting",
        required=False,
        kind="settings",
    )
    raw_settings = read_json(settings_path) if settings_path is not None else {}
    properties = extract_properties(mapping)
    field_declarations = list(walk_properties(properties))

    documents_path = input_directory / "documents.jsonl"
    raw_documents, documents_supplied, document_parse_issues = inspect_jsonl(documents_path)
    documents: list[DecodedSourceDocument] = []
    document_decode_issues: list[str] = []
    for sequence, document in enumerate(raw_documents, 1):
        try:
            documents.append(decode_document(document, sequence, "documents.jsonl"))
        except InputError as exc:
            document_decode_issues.append(str(exc))

    queries: list[ParsedQueryArtifact] = []
    for query_path in iter_json_files(input_directory / "queries"):
        source_file = str(query_path.relative_to(input_directory))
        try:
            body = read_json(query_path)
        except InputError as exc:
            queries.append(
                ParsedQueryArtifact(
                    query_id=query_path.stem,
                    source_file=source_file,
                    body=None,
                    error=str(exc),
                )
            )
            continue
        if not isinstance(body, dict):
            queries.append(
                ParsedQueryArtifact(
                    query_id=query_path.stem,
                    source_file=source_file,
                    body=None,
                    error="expected a Query DSL object",
                )
            )
            continue
        queries.append(
            ParsedQueryArtifact(
                query_id=query_path.stem,
                source_file=source_file,
                body=body,
            )
        )

    invalid_queries = sum(item.error is not None for item in queries)
    document_issues = document_parse_issues + document_decode_issues
    mapping_file = mapping_path.name
    settings_file = settings_path.name if settings_path is not None else None
    raw_index_name = source_index_name(mapping)
    return SourceInspection(
        source_id=source_id,
        index_name=raw_index_name or input_directory.name,
        source_index_name=raw_index_name,
        mapping=mapping,
        settings=raw_settings,
        settings_present=settings_path is not None,
        field_declarations=field_declarations,
        documents=documents,
        queries=queries,
        artifacts=[
            ArtifactRecord(
                "fields",
                mapping_file,
                len(field_declarations),
                len(field_declarations),
                0,
            ),
            ArtifactRecord(
                "queries",
                "queries/*.json",
                len(queries),
                len(queries) - invalid_queries,
                invalid_queries,
            ),
            ArtifactRecord(
                "documents",
                "documents.jsonl",
                documents_supplied,
                len(documents),
                len(document_issues),
            ),
        ],
        document_issues=document_issues,
        document_digest=file_sha256(documents_path),
        mapping_file=mapping_file,
        settings_file=settings_file,
    )


def extract_properties(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        raise InputError("mapping file must contain a JSON object")
    current = mapping.get("mappings", mapping)
    if isinstance(current, dict) and "properties" in current:
        properties = current["properties"]
    elif isinstance(current, dict):
        if len(current) > 1 and all(
            isinstance(item, dict) and "mappings" in item for item in current.values()
        ):
            raise InputError(
                "migrate2vespa assesses one source index per run. "
                "Export one index mapping at a time."
            )
        typed = [
            value for value in current.values()
            if isinstance(value, dict) and "properties" in value
        ]
        if len(typed) == 1:
            properties = typed[0]["properties"]
        elif len(mapping) == 1:
            return extract_properties(next(iter(mapping.values())))
        else:
            raise InputError("Could not find a unique properties object in mapping file")
    else:
        raise InputError("Could not find properties in mapping file")
    if not isinstance(properties, dict):
        raise InputError("mapping properties must be a JSON object")
    return properties


def source_index_name(mapping: Any) -> str | None:
    if not isinstance(mapping, dict) or "mappings" in mapping:
        return None
    wrapped = [
        name for name, value in mapping.items()
        if isinstance(value, dict) and "mappings" in value
    ]
    return wrapped[0] if len(wrapped) == 1 else None


def walk_properties(
    properties: dict[str, Any],
    prefix: str = "",
    parent_kind: str | None = None,
) -> Iterator[tuple[str, dict[str, Any], str | None]]:
    for short_name, raw_props in properties.items():
        props = raw_props if isinstance(raw_props, dict) else {}
        name = f"{prefix}.{short_name}" if prefix else short_name
        source_type = str(props.get("type", "object" if "properties" in props else "text"))
        yield name, props, parent_kind
        for multi_name, multi_props in props.get("fields", {}).items():
            yield f"{name}.{multi_name}", multi_props, "multi_field"
        if isinstance(props.get("properties"), dict):
            descendant_kind = (
                "nested" if parent_kind == "nested" or source_type == "nested"
                else source_type
            )
            yield from walk_properties(props["properties"], name, descendant_kind)


def decode_document(
    value: dict[str, Any],
    sequence: int,
    source_path: str = "documents.jsonl",
) -> DecodedSourceDocument:
    raw_fields = value.get("_source", value)
    if not isinstance(raw_fields, dict):
        raise InputError(
            f"Document {sequence} has a non-object _source; document type is inconsistent"
        )
    explicit_id = value.get("_id") if value.get("_id") is not None else raw_fields.get("id")
    synthetic = explicit_id is None
    if not synthetic and (
        isinstance(explicit_id, bool)
        or not isinstance(explicit_id, (str, int))
        or str(explicit_id) == ""
    ):
        raise InputError(
            f"Document {sequence} has an invalid _id/id; "
            "expected a non-empty string or integer"
        )
    source_id = f"doc-{sequence:06d}" if synthetic else str(explicit_id)
    return DecodedSourceDocument(
        source_id=source_id,
        fields=raw_fields,
        source_path=source_path,
        sequence=sequence,
        synthetic_id=synthetic,
    )
