from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from .manifest import MigrationManifest


class InputError(ValueError):
    """Raised when the local migration input contract is invalid."""

    def __init__(self, message: str, code: str = "INPUT_INVALID") -> None:
        super().__init__(message)
        self.code = code


def default_output_directory() -> Path:
    """Return the cwd-relative tool output directory (`./out`)."""
    return (Path.cwd() / "out").resolve()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"Required file not found: {path}", "INPUT_MISSING_FILE") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON in {path}: {exc}", "INPUT_INVALID_JSON") from exc


def inspect_jsonl(path: Path) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Return valid objects, nonblank line count, and line-specific errors."""
    documents: list[dict[str, Any]] = []
    if not path.exists():
        return documents, 0, []
    supplied = 0
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        supplied += 1
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSONL in {path.name} at line {line_number}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"Expected an object in {path.name} at line {line_number}")
            continue
        documents.append(value)
    return documents, supplied, errors


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    documents, _, errors = inspect_jsonl(path)
    if errors:
        raise InputError(errors[0])
    return documents


def file_sha256(path: Path) -> str | None:
    """Return a stable content identity for an optional local artifact."""
    if not path.exists():
        return None
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return f"sha256:{digest.hexdigest()}"


def write_yaml(path: Path, manifest: MigrationManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(manifest.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def read_manifest(path: Path) -> MigrationManifest:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"Migration manifest not found: {path}", "INPUT_MISSING_MANIFEST") from exc
    except yaml.YAMLError as exc:
        raise InputError(f"Invalid YAML in {path}: {exc}", "INPUT_INVALID_YAML") from exc
    if not isinstance(value, dict):
        raise InputError(f"Expected a YAML object in {path}")
    try:
        manifest = MigrationManifest.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise InputError(
            f"Invalid migration manifest in {path}: {exc}",
            "INPUT_INVALID_MANIFEST",
        ) from exc
    manifest.configured_input_directory = manifest.input_directory
    manifest.input_directory = str(_resolve_input_directory(path, manifest.input_directory))
    return manifest


def _resolve_input_directory(manifest_path: Path, configured: str) -> Path:
    candidate = Path(configured)
    if candidate.is_absolute():
        return candidate
    return (manifest_path.parent / candidate).resolve()


def sanitize_name(value: str, fallback: str = "migration") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"app_{cleaned}"
    return cleaned


def iter_json_files(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def resolve_input_json(
    directory: Path,
    *,
    exact_name: str,
    name_token: str,
    required: bool,
    kind: str,
) -> Path | None:
    """Resolve a top-level JSON input file by exact name, then ``*<token>*.json``.

    Prefers ``exact_name`` when present. Otherwise accepts a unique basename match
    containing ``name_token`` (case-insensitive), e.g. ``index-mappings.json`` for
    token ``mapping`` or ``index-settings.json`` for token ``setting``.
    """
    exact = directory / exact_name
    if exact.is_file():
        return exact

    token = name_token.lower()
    matches = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".json"
        and token in path.name.lower()
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise InputError(
            f"Ambiguous {kind} file in {directory}: found {names}. "
            f"Use exactly one match or rename to {exact_name}.",
            "INPUT_AMBIGUOUS_ARTIFACT",
        )
    if required:
        raise InputError(
            f"Required {kind} file not found in {directory}: "
            f"expected {exact_name} or a unique *{name_token}*.json",
            "INPUT_MISSING_MAPPING" if kind == "mapping" else "INPUT_MISSING_ARTIFACT",
        )
    return None
