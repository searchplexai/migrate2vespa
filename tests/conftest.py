from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def write_project(tmp_path: Path):
    def write(
        mapping: dict,
        *,
        settings: dict | None = None,
        documents: list[dict] | None = None,
        queries: dict[str, dict] | None = None,
        name: str = "project",
    ) -> Path:
        root = tmp_path / name
        root.mkdir()
        (root / "mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
        if settings is not None:
            (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        if documents is not None:
            (root / "documents.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in documents),
                encoding="utf-8",
            )
        if queries:
            query_root = root / "queries"
            query_root.mkdir()
            for query_name, body in queries.items():
                (query_root / f"{query_name}.json").write_text(
                    json.dumps(body), encoding="utf-8"
                )
        return root

    return write


@pytest.fixture
def simple_mapping() -> dict:
    return {
        "mappings": {
            "properties": {
                "title": {"type": "text"},
                "sku": {"type": "keyword"},
                "price": {"type": "double"},
            }
        }
    }
