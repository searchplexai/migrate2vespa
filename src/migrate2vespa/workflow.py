from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .analysis import analyze_inspection, portable_input_path
from .target.package import generate as generate_package
from .io import InputError, default_output_directory, read_manifest, write_yaml
from .manifest import MigrationManifest
from .sources.registry import get_source


def analyze_directory(
    input_directory: Path,
    output_directory: Path | None = None,
) -> MigrationManifest:
    input_directory = input_directory.resolve()
    output_directory = (output_directory or default_output_directory()).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    source = get_source()
    inspection = source.inspect(input_directory)
    manifest = analyze_inspection(
        source,
        inspection,
        input_directory,
        output_directory,
    )
    write_yaml(output_directory / "migration-manifest.yaml", manifest)
    return manifest


def analyze_input(
    source: Path,
    output_directory: Path | None = None,
) -> MigrationManifest:
    """Analyze either a migration directory or a standalone mapping JSON file."""
    source = source.resolve()
    output_directory = (output_directory or default_output_directory()).resolve()
    if source.is_dir():
        return analyze_directory(source, output_directory)
    if source.is_file() and source.suffix.lower() == ".json":
        with tempfile.TemporaryDirectory(prefix="migrate2vespa-mapping-") as temporary:
            isolated_root = Path(temporary) / source.stem
            isolated_root.mkdir()
            isolated = isolated_root / "mapping.json"
            shutil.copyfile(source, isolated)
            manifest = analyze_directory(isolated_root, output_directory)
            # Standalone mode is deliberately mapping-only. Retain truthful,
            # portable provenance instead of the temporary isolation directory.
            manifest.input_directory = portable_input_path(
                source.parent, output_directory
            )
            manifest.mapping_file = source.name
            write_yaml(output_directory / "migration-manifest.yaml", manifest)
            return manifest
    raise InputError("Input must be a migration directory or a mapping JSON file")


def generate_manifest(
    manifest_path: Path,
    output_directory: Path | None = None,
) -> Path:
    """Resolve the manifest's source implementation above shared generation."""
    manifest = read_manifest(manifest_path.resolve())
    try:
        source = get_source(manifest.source_id)
    except ValueError as exc:
        raise InputError(str(exc), "INPUT_INVALID_MANIFEST") from exc
    return generate_package(
        manifest_path,
        output_directory,
        source_decoder=source,
    )
