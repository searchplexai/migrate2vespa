from __future__ import annotations

import json
from pathlib import Path

import pytest

from migrate2vespa.io import read_manifest
from migrate2vespa.manifest import Decision, GenerationScope
from migrate2vespa.target.package import GenerationError, package_fingerprint
from migrate2vespa.workflow import analyze_input, generate_manifest


def test_quickstart_generates_pyvespa_package_and_feed(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "quickstart"
    output = tmp_path / "out"

    analyze_input(fixture, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    generated = read_manifest(output / "migration-manifest.yaml")

    schema = (output / "vespa-app" / "schemas" / "quickstart.sd").read_text()
    services = (output / "vespa-app" / "services.xml").read_text()
    feed_lines = (output / "vespa-app" / "feed" / "documents.jsonl").read_text().splitlines()
    first = json.loads(feed_lines[0])

    assert generated.generation.status == "READY"
    assert generated.validation["package_digest"].startswith("sha256:")
    assert "schema quickstart" in schema
    assert "field title__raw type string" in schema
    assert "field tags type array<string>" in schema
    assert "field embedding type tensor<float>(x[8])" in schema
    assert "rank-profile embedding_ann" in schema
    assert "container id=\"quickstart_container\"" in services
    assert len(feed_lines) == 50
    assert first["fields"]["migrate2vespa_source_id"] == "1"
    assert len(first["fields"]["embedding"]["values"]) == 8


def test_generation_applies_date_normalizer_array_and_vector_transforms(write_project):
    mapping = {
        "mappings": {"properties": {
            "sku": {"type": "keyword", "normalizer": "folded"},
            "created": {"type": "date", "format": "strict_date_optional_time"},
            "tags": {"type": "keyword"},
            "embedding": {"type": "dense_vector", "dims": 2, "similarity": "cosine", "index": True},
        }}
    }
    settings = {"settings": {"analysis": {"normalizer": {
        "folded": {"type": "custom", "filter": ["lowercase"]}
    }}}}
    root = write_project(
        mapping,
        settings=settings,
        documents=[{"_id": "A", "_source": {
            "sku": "ABC", "created": "2026-01-01T00:00:00Z",
            "tags": ["one", "two"], "embedding": [0.25, -0.5],
        }}],
    )
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    operation = json.loads(
        (output / "vespa-app" / "feed" / "documents.jsonl").read_text()
    )
    assert operation["fields"]["sku"] == "abc"
    assert operation["fields"]["created"] == 1767225600
    assert operation["fields"]["tags"] == ["one", "two"]
    assert operation["fields"]["embedding"] == {"values": [0.25, -0.5]}
    assert operation["put"].endswith("::A")


def test_isolated_unsupported_field_produces_partial_package(write_project):
    mapping = {"mappings": {"properties": {
        "sku": {"type": "keyword"},
        "mystery": {"type": "plugin_field"},
    }}}
    root = write_project(mapping, documents=[{"id": "1", "sku": "A", "mystery": "x"}])
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    manifest = read_manifest(output / "migration-manifest.yaml")
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()

    assert manifest.generation.status == "PARTIAL"
    assert [item.source_path for item in manifest.generation.omitted] == ["mystery"]
    assert "field sku type string" in schema
    assert "field mystery" not in schema


def test_nested_subtree_is_omitted_as_a_unit_without_blocking_the_package(write_project):
    mapping = {"mappings": {"properties": {
        "title": {"type": "text"},
        "sku": {"type": "keyword"},
        "variants": {"type": "nested", "properties": {
            "color": {"type": "keyword"},
            "size": {"type": "integer"},
        }},
    }}}
    root = write_project(mapping, documents=[{"_id": "1", "_source": {
        "title": "Shoe", "sku": "A", "variants": [{"color": "red", "size": 42}],
    }}])
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    manifest = read_manifest(output / "migration-manifest.yaml")
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    operation = json.loads((output / "vespa-app" / "feed" / "documents.jsonl").read_text())
    child = next(field for field in manifest.fields if field.source_name == "variants.color")

    assert manifest.generation.status == "PARTIAL"
    assert manifest.generation.package_blockers == []
    assert [item.source_path for item in manifest.generation.omitted] == ["variants"]
    omission = manifest.generation.omitted[0]
    assert omission.scope is GenerationScope.SUBTREE
    assert omission.covers == ("variants", "variants.color", "variants.size")
    assert "field title type string" in schema
    assert "variants" not in schema
    assert "variants" not in operation["fields"]
    assert child.decision is Decision.REVIEW
    assert "nested structure variants" in child.reasons[0]


def test_name_collision_blocks_and_publishes_no_current_package(write_project):
    mapping = {"mappings": {"properties": {
        "a-b": {"type": "keyword"},
        "a_b": {"type": "keyword"},
    }}}
    root = write_project(mapping)
    output = root / "out"
    analyze_input(root, output)

    with pytest.raises(GenerationError) as error:
        generate_manifest(output / "migration-manifest.yaml", output)

    assert error.value.code == "GENERATION_SCHEMA_BLOCKED"
    assert not (output / "vespa-app").exists()
    assert read_manifest(output / "migration-manifest.yaml").generation.status == "BLOCKED"


def test_package_output_is_deterministic(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "quickstart"
    digests = []
    for name in ("one", "two"):
        output = tmp_path / name
        analyze_input(fixture, output)
        generate_manifest(output / "migration-manifest.yaml", output)
        digests.append(package_fingerprint(output / "vespa-app"))
    assert digests[0] == digests[1]


def test_changed_documents_block_regeneration_without_current_package(write_project):
    mapping = {"mappings": {"properties": {"sku": {"type": "keyword"}}}}
    root = write_project(mapping, documents=[{"id": "1", "sku": "A"}])
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    with (root / "documents.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": "2", "sku": "B"}) + "\n")

    with pytest.raises(GenerationError) as error:
        generate_manifest(output / "migration-manifest.yaml", output)

    assert error.value.code == "GENERATION_SOURCE_CHANGED"
    assert not (output / "vespa-app").exists()
    assert (output / "vespa-app.stale").is_dir()
