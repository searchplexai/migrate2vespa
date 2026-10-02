from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from migrate2vespa.io import read_manifest
from migrate2vespa.manifest import Decision, GenerationScope, RequiredCapability
from migrate2vespa.reporting import render_console_summary
from migrate2vespa.sources.elastic.fields import assess_field
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

    assert generated.generation.status == "PARTIAL"
    title = next(field for field in generated.fields if field.source_name == "title")
    assert title.confidence["behavioral_equivalence"] == "LOW"
    assert "description" in {item.source_path for item in generated.generation.omitted}
    analyzer_query = next(item for item in generated.queries if item.name == "03-custom-analyzer")
    assert any("not generated" in reason and "linguistics" in reason for reason in analyzer_query.reasons)
    assert not any("match semantics do not apply" in reason for reason in analyzer_query.reasons)
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


def test_query_cannot_reenable_a_field_declared_index_false(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "sku": {"type": "keyword"},
            "title": {"type": "text", "index": False},
        }}},
        queries={"search": {"query": {"match": {"title": "shoe"}}}},
    )
    output = root / "out"
    manifest = analyze_input(root, output)
    title = next(field for field in manifest.fields if field.source_name == "title")
    assert title.decision is Decision.REVIEW
    assert "query_conflicts_with_index_disabled" in title.risks
    assert RequiredCapability.TEXT_MATCH not in title.required_capabilities
    assert manifest.source_signals["text_analysis"]["assessed"] == 0
    assert manifest.queries[0].decision is Decision.REVIEW

    generate_manifest(output / "migration-manifest.yaml", output)
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    title_definition = schema.split("field title type string {", 1)[1].split("}", 1)[0]
    assert "indexing: summary" in title_definition
    assert "indexing: summary | index" not in title_definition
    assert "enable-bm25" not in schema


@pytest.mark.parametrize(
    ("field_type", "query", "forbidden_capabilities"),
    [
        ("keyword", {"term": {"value": "A"}}, {RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER}),
        ("integer", {"range": {"value": {"gte": 1}}}, {RequiredCapability.RANGE, RequiredCapability.FILTER}),
        ("boolean", {"term": {"value": True}}, {RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER}),
    ],
)
def test_query_cannot_reenable_lookup_when_index_and_doc_values_are_disabled(
    write_project, field_type, query, forbidden_capabilities
):
    root = write_project(
        {"mappings": {"properties": {
            "sku": {"type": "keyword"},
            "value": {"type": field_type, "index": False, "doc_values": False},
        }}},
        queries={"lookup": {"query": query}},
    )
    output = root / "out"
    manifest = analyze_input(root, output)
    field = next(item for item in manifest.fields if item.source_name == "value")
    assert field.decision is Decision.REVIEW
    assert "query_conflicts_with_lookup_disabled" in field.risks
    assert not forbidden_capabilities.intersection(field.required_capabilities)
    assert manifest.queries[0].decision is Decision.REVIEW

    generate_manifest(output / "migration-manifest.yaml", output)
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    definition = schema.split(f"field value type {field.target_plan.type} {{", 1)[1].split("}", 1)[0]
    assert "indexing: summary" in definition
    assert "attribute" not in definition


def test_keyword_with_index_disabled_remains_queryable_via_doc_values(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "value": {"type": "keyword", "index": False},
        }}},
        queries={"lookup": {"query": {"term": {"value": "A"}}}},
    )
    field = next(item for item in analyze_input(root, root / "out").fields if item.source_name == "value")
    assert "query_conflicts_with_lookup_disabled" not in field.risks
    assert RequiredCapability.EXACT_MATCH in field.required_capabilities
    assert "attribute" in field.target_plan.indexing


def test_explicit_float_vector_generates_but_index_options_remain_unresolved(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "sku": {"type": "keyword"},
            "embedding": {
                "type": "dense_vector", "dims": 2,
                "element_type": "float", "similarity": "cosine", "index": True,
            },
        }}},
        documents=[{"_id": "1", "_source": {"sku": "A", "embedding": [0.25, -0.5]}}],
    )
    output = root / "out"
    manifest = analyze_input(root, output)
    vector = next(field for field in manifest.fields if field.source_name == "embedding")
    assert vector.support.generate
    generate_manifest(output / "migration-manifest.yaml", output)
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    assert "field embedding type tensor<float>(x[2])" in schema

    unsupported = assess_field(
        "embedding",
        {"type": "dense_vector", "dims": 2, "index_options": {"type": "hnsw"}},
        [], None, {},
    )
    assert not unsupported.support.generate
    assert "ES-UNSUPPORTED-001" in unsupported.rules


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


def test_copy_to_does_not_generate_an_unpopulated_destination(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "sku": {"type": "keyword"},
            "title": {"type": "text", "copy_to": "all_text"},
            "all_text": {"type": "text"},
        }}},
        documents=[{"_id": "1", "_source": {"sku": "A", "title": "Shoe"}}],
    )
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    manifest = read_manifest(output / "migration-manifest.yaml")
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    assert manifest.generation.status == "PARTIAL"
    assert {item.source_path for item in manifest.generation.omitted} == {"all_text"}
    assert "field title type string" in schema
    assert "field all_text" not in schema
    assert "field sku type string" in schema


def test_scalar_object_children_flatten_but_object_arrays_do_not(write_project):
    mapping = {"mappings": {"properties": {
        "sku": {"type": "keyword"},
        "details": {"properties": {
            "color": {"type": "keyword"},
            "address": {"properties": {"city": {"type": "keyword"}}},
        }},
    }}}
    scalar = write_project(
        mapping, documents=[{"_id": "1", "_source": {
            "sku": "A", "details": {"color": "red", "address": {"city": "London"}},
        }}],
        name="scalar",
    )
    scalar_output = scalar / "out"
    analyze_input(scalar, scalar_output)
    generate_manifest(scalar_output / "migration-manifest.yaml", scalar_output)
    scalar_schema = (scalar_output / "vespa-app" / "schemas" / "scalar.sd").read_text()
    scalar_feed = json.loads((scalar_output / "vespa-app" / "feed" / "documents.jsonl").read_text())
    assert "field details__color type string" in scalar_schema
    assert "field details__address__city type string" in scalar_schema
    assert scalar_feed["fields"]["details__color"] == "red"
    scalar_manifest = read_manifest(scalar_output / "migration-manifest.yaml")
    assert scalar_manifest.generation.status == "READY"
    assert scalar_manifest.generation.omitted == []
    assert scalar_manifest.generation.generated_field_count == 3
    assert scalar_manifest.generation.supplied_field_count == 3
    summary = render_console_summary(
        scalar_manifest,
        generation_status="READY",
        manifest_path="out/migration-manifest.yaml",
    )
    assert "OMITTED FROM GENERATED APP" not in summary
    assert "object is not recognized" not in summary

    array = write_project(
        mapping, documents=[{"_id": "1", "_source": {"sku": "A", "details": [{"color": "red"}]}}],
        name="array",
    )
    array_output = array / "out"
    analyze_input(array, array_output)
    generate_manifest(array_output / "migration-manifest.yaml", array_output)
    array_manifest = read_manifest(array_output / "migration-manifest.yaml")
    assert {item.source_path for item in array_manifest.generation.omitted} == {"details"}


def test_multifield_ignore_above_omits_only_the_overlong_search_value(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "title": {"type": "text", "fields": {"raw": {"type": "keyword", "ignore_above": 3}}},
        }}},
        documents=[
            {"_id": "1", "_source": {"title": "long"}},
            {"_id": "2", "_source": {"title": "ok"}},
        ],
    )
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    records = [json.loads(line) for line in (output / "vespa-app" / "feed" / "documents.jsonl").read_text().splitlines()]
    assert "title__raw" not in records[0]["fields"]
    assert records[1]["fields"]["title__raw"] == "ok"


def test_ignore_above_with_mixed_cardinality_does_not_block_package(write_project):
    root = write_project(
        {"mappings": {"properties": {
            "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 3}}},
        }}},
        documents=[
            {"_id": "1", "_source": {"title": "long"}},
            {"_id": "2", "_source": {"title": ["ok", "wide"]}},
        ],
    )
    output = root / "out"
    analyze_input(root, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    records = [
        json.loads(line)
        for line in (output / "vespa-app" / "feed" / "documents.jsonl").read_text().splitlines()
    ]
    assert "title__keyword" not in records[0]["fields"]
    assert records[1]["fields"]["title__keyword"] == ["ok"]


def test_reviewed_manifest_can_widen_a_numeric_target_type(write_project):
    root = write_project(
        {"mappings": {"properties": {"stock": {"type": "integer"}}}},
        documents=[{"_id": "1", "_source": {"stock": 7}}],
    )
    output = root / "out"
    analyze_input(root, output)
    manifest_path = output / "migration-manifest.yaml"
    value = yaml.safe_load(manifest_path.read_text())
    value["fields"]["stock"]["target"]["type"] = "long"
    manifest_path.write_text(yaml.safe_dump(value, sort_keys=False))

    generate_manifest(manifest_path, output)
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    assert "field stock type long" in schema


def test_reviewed_manifest_can_omit_a_field_without_losing_coverage(write_project):
    root = write_project({"mappings": {"properties": {
        "sku": {"type": "keyword"}, "stock": {"type": "integer"},
    }}})
    output = root / "out"
    analyze_input(root, output)
    manifest_path = output / "migration-manifest.yaml"
    value = yaml.safe_load(manifest_path.read_text())
    value["fields"]["stock"]["support"]["generate"] = False
    manifest_path.write_text(yaml.safe_dump(value, sort_keys=False))

    generate_manifest(manifest_path, output)
    manifest = read_manifest(manifest_path)
    schema = (output / "vespa-app" / "schemas" / "project.sd").read_text()
    assert manifest.coverage.fields_assessed == 2
    assert manifest.generation.status == "PARTIAL"
    assert "field stock" not in schema


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
