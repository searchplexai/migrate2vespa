from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..io import (
    default_output_directory,
    file_sha256,
    read_jsonl,
    read_manifest,
    write_yaml,
)
from ..manifest import (
    FieldAssessment,
    MigrationManifest,
    TargetFieldPlan,
    value_at_path,
)
from ..sources.contract import DecodedSourceDocument, DocumentDecoder
from .feed_validation import FeedValidationError, validate_document_values
from .planner import validate_field_plan
from .selection import build_generation_plan, retained_fields
from .transforms import TransformError, apply_transforms, validate_transform


class GenerationError(ValueError):
    """Raised when a safe Vespa schema cannot be produced from the manifest."""

    def __init__(self, message: str, code: str = "GENERATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


class GenerationBlocked(GenerationError):
    """Raised when supplied evidence cannot produce a safe package."""


def schema_blockers(manifest: MigrationManifest) -> list[str]:
    plan = build_generation_plan(manifest)
    blockers: list[str] = list(plan.package_blockers)
    if not manifest.coverage.all_supplied_accounted_for:
        blockers.append("Artifact coverage is incomplete")
    retained = retained_fields(manifest, plan)
    target_names = [
        field.target_plan.name
        for field in retained
        if field.target_plan is not None
    ]
    invalid = sorted(
        name for name in target_names
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
    )
    duplicates = sorted({name for name in target_names if target_names.count(name) > 1})
    if invalid:
        blockers.append("Invalid Vespa field identifiers: " + ", ".join(invalid))
    if duplicates:
        blockers.append("Conflicting Vespa field identifiers: " + ", ".join(duplicates))
    if "migrate2vespa_source_id" in target_names:
        blockers.append("Source field conflicts with reserved field migrate2vespa_source_id")
    for field in retained:
        try:
            _validate_target_plan(field.target_plan)
            for issue in validate_field_plan(field, field.target_plan):
                blockers.append(f"{field.source_name}: {issue}")
            for transform in field.target_plan.transforms:
                validate_transform(transform)
        except TransformError as exc:
            blockers.append(f"{field.source_name}: {exc}")
    return list(dict.fromkeys(blockers))


def _validate_target_plan(plan: TargetFieldPlan) -> None:
    if not plan.name or not plan.type or not plan.source_path:
        raise TransformError("Target plan requires name, type, and source_path")
    allowed_indexing = {"summary", "attribute", "index"}
    unsupported_indexing = sorted(set(plan.indexing) - allowed_indexing)
    if not plan.indexing or unsupported_indexing:
        detail = ", ".join(unsupported_indexing) or "none"
        raise TransformError(f"Unsupported Vespa indexing directives: {detail}")
    if len(set(plan.indexing)) != len(plan.indexing):
        raise TransformError("Target indexing directives must not contain duplicates")
    if plan.rank not in {None, "filter"}:
        raise TransformError(f"Unsupported Vespa rank setting: {plan.rank}")
    if plan.bm25 and "index" not in plan.indexing:
        raise TransformError("BM25 requires index in target indexing")
    if plan.match is not None:
        if plan.match.mode not in {"text", "word", "exact"}:
            raise TransformError(f"Unsupported Vespa match mode: {plan.match.mode}")
        if plan.match.case not in {None, "cased", "uncased"}:
            raise TransformError(f"Unsupported Vespa match case: {plan.match.case}")
    if plan.ann is not None and plan.ann.hnsw_enabled and not {
        "attribute", "index"
    }.issubset(plan.indexing):
        raise TransformError("HNSW requires attribute and index in target indexing")


def _construct_package_with_pyvespa(
    app_directory: Path,
    manifest: MigrationManifest,
    generated: list[FieldAssessment],
) -> None:
    """Construct the application package with PyVespa as the sole constructor."""
    try:
        from vespa.package import (
            HNSW,
            ApplicationPackage,
            Document,
            Field,
            RankProfile,
            Schema,
        )
    except ImportError as exc:
        raise GenerationError(
            "PyVespa is required to generate a package. Reinstall migrate2vespa with its declared dependencies.",
            "GENERATION_DEPENDENCY_MISSING",
        ) from exc
    try:
        package_name = "".join(ch for ch in manifest.project_name if ch.isalnum()).lower()[:20] or "migration"
        app_directory.mkdir(parents=True, exist_ok=True)
        fields = [Field(name="migrate2vespa_source_id", type="string", indexing=["attribute", "summary"])]
        for item in generated:
            plan = _target_plan(item)
            kwargs: dict[str, Any] = {}
            if plan.bm25:
                kwargs["index"] = "enable-bm25"
            if plan.match and not (plan.match.mode == "text" and plan.match.case is None):
                kwargs["match"] = plan.match.pyvespa_properties()
            if plan.rank is not None:
                kwargs["rank"] = plan.rank
            if plan.ann is not None and plan.ann.hnsw_enabled:
                kwargs["ann"] = HNSW(distance_metric=plan.ann.distance_metric)
            fields.append(Field(name=plan.name, type=plan.type, indexing=list(plan.indexing), **kwargs))
        schema = Schema(name=manifest.project_name, document=Document(fields=fields))
        package = ApplicationPackage(
            name=package_name,
            schema=[schema],
            create_query_profile_by_default=False,
        )
        lexical_fields = [
            _target_plan(field).name
            for field in generated
            if _target_plan(field).bm25
        ]
        if lexical_fields:
            schema.add_rank_profile(
                RankProfile(name="default", first_phase=" + ".join(f"bm25({name})" for name in lexical_fields))
            )
        for field in generated:
            plan = _target_plan(field)
            if plan.ann is None or not plan.ann.hnsw_enabled:
                continue
            schema.add_rank_profile(
                RankProfile(
                    name=f"{plan.name}_ann",
                    inputs=[(f"query({plan.name}_query)", plan.type)],
                    first_phase=f"closeness(field, {plan.name})",
                )
            )
        package.to_files(str(app_directory))
    except Exception as exc:
        raise GenerationError(f"PyVespa could not construct the package: {exc}") from exc


def generate(
    manifest_path: Path,
    output_directory: Path | None = None,
    *,
    source_decoder: DocumentDecoder,
) -> Path:
    """Generate a draft Vespa app and local fixtures from a reviewed manifest."""
    manifest_path = manifest_path.resolve()
    output_directory = (output_directory or default_output_directory()).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    app_directory = output_directory / "vespa-app"
    stale_directory: Path | None = None

    manifest = read_manifest(manifest_path)

    input_digest = manifest_fingerprint(manifest)
    _invalidate_generated_validation(manifest, input_digest)
    # Persist invalidation before attempting generation so a failed regeneration
    # cannot leave validation claims attached to an older artifact.
    write_yaml(manifest_path, manifest)
    # Remove any previous package from the live path before evaluating the new
    # manifest. If generation fails, the old package remains available under a
    # clearly stale name instead of being mistaken for the current result.
    stale_directory = _quarantine_existing_package(app_directory)
    plan = build_generation_plan(manifest)
    manifest.generation = plan
    blockers = schema_blockers(manifest)
    if blockers:
        manifest.generation.status = "BLOCKED"
        manifest.generation.planned_outcome = "BLOCKED"
        manifest.generation.package_blockers = blockers
        manifest.validation["generation"] = "BLOCKED"
        manifest.validation["generation_blocker_count"] = len(blockers)
        if stale_directory is not None:
            manifest.validation["stale_package"] = stale_directory.name
        write_yaml(manifest_path, manifest)
        state = "blocker remains" if len(blockers) == 1 else "blockers remain"
        lines = ["Cannot generate a safe Vespa package.", "", f"{len(blockers)} schema {state}:", ""]
        lines.extend(f"- {item}" for item in blockers)
        lines.extend(["", "See migration-manifest.yaml for details."])
        raise GenerationError("\n".join(lines), "GENERATION_SCHEMA_BLOCKED")

    staging_root = Path(tempfile.mkdtemp(prefix=".vespa-app.tmp-", dir=output_directory))
    staging_app = staging_root / "vespa-app"
    generated = retained_fields(manifest, plan)
    try:
        _construct_package_with_pyvespa(staging_app, manifest, generated)
        schema_directory = staging_app / "schemas"
        feed_directory = staging_app / "feed"
        schema_directory.mkdir(parents=True, exist_ok=True)
        feed_directory.mkdir(parents=True, exist_ok=True)
        _write_documents(
            manifest,
            feed_directory / "documents.jsonl",
            source_decoder=source_decoder,
            included_source_paths={field.source_name for field in generated},
        )
        # A directory rename is atomic when source and destination share the
        # same filesystem, which they do because staging_root lives in output.
        os.replace(staging_app, app_directory)
        shutil.rmtree(staging_root, ignore_errors=True)
        if stale_directory is not None:
            _remove_path(stale_directory)
    except GenerationBlocked as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        manifest.generation.status = "BLOCKED"
        manifest.generation.planned_outcome = "BLOCKED"
        manifest.generation.package_blockers = [str(exc)]
        manifest.validation["generation"] = "BLOCKED"
        manifest.validation["generation_blocker_count"] = 1
        if stale_directory is not None:
            manifest.validation["stale_package"] = stale_directory.name
        write_yaml(manifest_path, manifest)
        raise
    except GenerationError as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        manifest.generation.status = "ERROR"
        manifest.validation["generation"] = "ERROR"
        manifest.validation["generation_error"] = str(exc)
        if stale_directory is not None:
            manifest.validation["stale_package"] = stale_directory.name
        write_yaml(manifest_path, manifest)
        raise
    except Exception as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        message = f"PyVespa could not construct the package: {exc}"
        manifest.generation.status = "ERROR"
        manifest.validation["generation"] = "ERROR"
        manifest.validation["generation_error"] = message
        if stale_directory is not None:
            manifest.validation["stale_package"] = stale_directory.name
        write_yaml(manifest_path, manifest)
        raise GenerationError(message) from exc

    outcome = plan.planned_outcome
    manifest.generation.status = outcome
    manifest.validation["generation"] = outcome
    manifest.validation["package_digest"] = package_fingerprint(app_directory)
    manifest.validation.pop("generation_blocker_count", None)
    manifest.validation.pop("generation_error", None)
    manifest.validation.pop("stale_package", None)
    write_yaml(manifest_path, manifest)
    return output_directory


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _quarantine_existing_package(app_directory: Path) -> Path | None:
    """Move the previous package aside so a failed run cannot look current."""
    if not app_directory.exists():
        return None
    stale_directory = app_directory.parent / "vespa-app.stale"
    _remove_path(stale_directory)
    os.replace(app_directory, stale_directory)
    return stale_directory

def manifest_fingerprint(manifest: MigrationManifest) -> str:
    """Hash generation-relevant manifest content while excluding validation state."""
    value = manifest.to_dict()
    value.pop("validation", None)
    value.pop("generation", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def package_fingerprint(app_directory: Path) -> str:
    """Hash all static package files in stable path order."""
    digest = hashlib.sha256()
    for path in sorted(item for item in app_directory.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(app_directory)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _invalidate_generated_validation(manifest: MigrationManifest, input_digest: str) -> None:
    manifest.validation = {
        "analysis": (
            "COMPLETE"
            if manifest.coverage.all_supplied_accounted_for
            else "INCOMPLETE"
        ),
        "generation": "NOT_RUN",
        "input_digest": input_digest,
        "package_digest": None,
    }


def transform_document(
    document: DecodedSourceDocument,
    manifest: MigrationManifest,
    included_source_paths: set[str] | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {"migrate2vespa_source_id": document.source_id}
    for assessment in manifest.fields:
        if included_source_paths is not None and assessment.source_name not in included_source_paths:
            continue
        if not _field_is_generated(assessment):
            continue
        plan = _target_plan(assessment)
        value = value_at_path(document.fields, plan.source_path)
        if value is None:
            continue
        output[plan.name] = apply_transforms(value, plan.transforms)
    return {
        "put": f"id:{manifest.project_name}:{manifest.project_name}::{document.source_id}",
        "fields": output,
    }


def _write_documents(
    manifest: MigrationManifest,
    output_path: Path,
    *,
    source_decoder: DocumentDecoder,
    included_source_paths: set[str] | None = None,
) -> None:
    if manifest.document_count == 0:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("", encoding="utf-8")
        return
    documents_path = manifest.input_path / "documents.jsonl"
    actual_digest = file_sha256(documents_path)
    if actual_digest != manifest.document_digest:
        raise GenerationBlocked(
            "documents.jsonl changed after analysis; rerun migrate2vespa on the input directory",
            "GENERATION_SOURCE_CHANGED",
        )
    raw_documents = read_jsonl(documents_path)
    documents = [
        source_decoder.decode_document(document, index, "documents.jsonl")
        for index, document in enumerate(raw_documents, 1)
    ]
    if len(documents) != manifest.document_count:
        raise GenerationBlocked(
            "documents.jsonl no longer matches the analyzed manifest: "
            f"expected {manifest.document_count} valid document(s), found {len(documents)}",
            "GENERATION_SOURCE_CHANGED",
        )
    plans = {
        assessment.target_plan.name: assessment.target_plan
        for assessment in manifest.fields
        if assessment.target_plan is not None
        and _field_is_generated(assessment)
        and (
            included_source_paths is None
            or assessment.source_name in included_source_paths
        )
    }
    seen_source_ids: set[str] = set()
    lines: list[str] = []
    for document_number, document in enumerate(documents, 1):
        if document.source_id in seen_source_ids:
            raise GenerationBlocked(
                f"Generated feed contains duplicate source document ID: {document.source_id}",
                "GENERATION_FEED_INVALID",
            )
        seen_source_ids.add(document.source_id)
        operation = transform_document(document, manifest, included_source_paths)
        try:
            validate_document_values(document_number, operation["fields"], plans)
        except FeedValidationError as exc:
            raise GenerationBlocked(
                f"Generated feed value is incompatible with the target plan: {exc}",
                "GENERATION_FEED_INVALID",
            ) from exc
        lines.append(json.dumps(operation, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _field_is_generated(field: FieldAssessment) -> bool:
    return field.target_plan is not None and field.support.generate


def _target_plan(field: FieldAssessment) -> TargetFieldPlan:
    if field.target_plan is None:
        raise GenerationError(f"Field {field.source_name} has no resolved target plan")
    return field.target_plan
