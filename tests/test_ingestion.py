from __future__ import annotations

import json
from pathlib import Path

import pytest

from migrate2vespa.io import InputError
from migrate2vespa.sources.elastic import ElasticSource
from migrate2vespa.workflow import analyze_input


def test_direct_and_index_wrapped_mapping_shapes(write_project, simple_mapping):
    direct = write_project(simple_mapping, name="direct")
    wrapped = write_project({"products-v3": simple_mapping}, name="wrapped")

    direct_inspection = ElasticSource().inspect(direct)
    wrapped_inspection = ElasticSource().inspect(wrapped)

    assert [item[0] for item in direct_inspection.field_declarations] == [
        "title", "sku", "price"
    ]
    assert wrapped_inspection.source_index_name == "products-v3"


def test_multiple_indices_are_rejected(write_project, simple_mapping):
    root = write_project({"one": simple_mapping, "two": simple_mapping})
    with pytest.raises(InputError, match="one source index"):
        ElasticSource().inspect(root)


def test_optional_malformed_query_is_assessed_not_fatal(write_project, simple_mapping):
    root = write_project(simple_mapping)
    query_root = root / "queries"
    query_root.mkdir()
    (query_root / "broken.json").write_text("{", encoding="utf-8")

    manifest = analyze_input(root, root / "out")

    assert manifest.coverage.queries_invalid == 1
    assert manifest.coverage.all_supplied_accounted_for
    assert manifest.queries[0].migration_signals == ["invalid_query"]


def test_documents_accept_source_and_simple_shapes_with_synthetic_ids(
    write_project, simple_mapping
):
    root = write_project(
        simple_mapping,
        documents=[
            {"_id": "A", "_source": {"title": "One", "sku": "A", "price": 1.0}},
            {"id": "B", "title": "Two", "sku": "B", "price": 2.0},
            {"title": "Three", "sku": "C", "price": 3.0},
        ],
    )

    inspection = ElasticSource().inspect(root)

    assert [item.source_id for item in inspection.documents] == ["A", "B", "doc-000003"]
    assert inspection.documents[-1].synthetic_id


def test_standalone_mapping_is_mapping_only(write_project, simple_mapping, tmp_path):
    root = write_project(simple_mapping)
    output = tmp_path / "standalone-out"

    manifest = analyze_input(root / "mapping.json", output)

    assert manifest.coverage.fields_assessed == 3
    assert manifest.coverage.documents_supplied == 0
    assert manifest.coverage.queries_supplied == 0
    assert manifest.mapping_file == "mapping.json"
    assert (output / "migration-manifest.yaml").is_file()


def test_malformed_mapping_has_stable_input_error(write_project):
    root = write_project({"mappings": {"properties": {}}})
    (root / "mapping.json").write_text("{", encoding="utf-8")
    with pytest.raises(InputError) as error:
        ElasticSource().inspect(root)
    assert error.value.code == "INPUT_INVALID_JSON"
