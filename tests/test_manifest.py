from __future__ import annotations

import copy

import pytest
import yaml

from migrate2vespa.io import InputError, read_manifest, write_yaml
from migrate2vespa.manifest import FORMAT_VERSION, EvidenceLevel, MigrationManifest
from migrate2vespa.workflow import analyze_input


def test_format_v1_manifest_round_trips_typed_claims(write_project, simple_mapping):
    root = write_project(simple_mapping)
    output = root / "out"
    original = analyze_input(root, output)
    serialized = original.to_dict()

    loaded = read_manifest(output / "migration-manifest.yaml")
    round_tripped = loaded.to_dict()

    assert serialized == round_tripped
    claim = loaded.fields[0].source_type_claim
    assert claim is not None
    assert claim.evidence_level is EvidenceLevel.DECLARED
    assert claim.source.startswith("mapping.properties.")
    assert serialized["format_version"] == FORMAT_VERSION == 1


def test_manifest_rejects_any_other_format(write_project, simple_mapping):
    root = write_project(simple_mapping)
    output = root / "out"
    manifest = analyze_input(root, output).to_dict()
    manifest["format_version"] = 9
    path = output / "invalid.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(InputError, match="format_version"):
        read_manifest(path)


def test_raw_source_index_and_target_schema_are_distinct(write_project, simple_mapping):
    raw_name = "Products-Live.2026"
    root = write_project({raw_name: simple_mapping})
    manifest = analyze_input(root, root / "out")
    value = manifest.to_dict()

    assert value["source"]["index"] == raw_name
    assert value["target"]["schema"] == "products_live_2026"


def test_manifest_serialization_is_byte_stable(write_project, simple_mapping):
    root = write_project(simple_mapping)
    output = root / "out"
    manifest = analyze_input(root, output)
    first = (output / "migration-manifest.yaml").read_bytes()
    write_yaml(output / "migration-manifest.yaml", manifest)
    second = (output / "migration-manifest.yaml").read_bytes()
    assert first == second


def test_manifest_count_validation_rejects_unaccounted_artifacts(write_project, simple_mapping):
    root = write_project(simple_mapping)
    value = analyze_input(root, root / "out").to_dict()
    value["coverage"]["fields"]["unassessed"] = 1
    with pytest.raises(ValueError, match="coverage counts"):
        MigrationManifest.from_dict(copy.deepcopy(value))
