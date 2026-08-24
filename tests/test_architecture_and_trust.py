from __future__ import annotations

import ast
import socket
from pathlib import Path

from migrate2vespa.manifest import (
    Decision,
    FieldAssessment,
    LogicalType,
    RequiredCapability,
)
from migrate2vespa.target.planner import apply_plan
from migrate2vespa.workflow import analyze_input, generate_manifest


ROOT = Path(__file__).parents[1]
SOURCE_ROOT = ROOT / "src" / "migrate2vespa"


def test_source_implementation_does_not_import_target_plan_types():
    forbidden = {"TargetFieldPlan", "MatchPlan", "AnnPlan"}
    for path in (SOURCE_ROOT / "sources").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not forbidden.intersection(imported), path


def test_planner_depends_only_on_normalized_facts():
    one = FieldAssessment(
        source_name="product-code",
        source_type="keyword",
        decision=Decision.DIRECT,
        rules=["ES-FIELD-KEYWORD-001"],
        logical_type=LogicalType.STRING,
        required_capabilities=[RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER],
    )
    two = FieldAssessment(
        source_name="product-code",
        source_type="purple_elephant",
        decision=Decision.REDESIGN,
        rules=["FUTURE-999"],
        logical_type=LogicalType.STRING,
        required_capabilities=[RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER],
    )
    assert apply_plan(one) == apply_plan(two) == ()
    assert one.target_plan == two.target_plan


def test_analysis_and_generation_make_no_network_calls(tmp_path, monkeypatch):
    fixture = ROOT / "fixtures" / "quickstart"
    output = tmp_path / "out"

    def blocked_socket(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    analyze_input(fixture, output)
    generate_manifest(output / "migration-manifest.yaml", output)
    assert (output / "vespa-app" / "services.xml").is_file()


def test_pyvespa_is_the_only_package_constructor():
    package_source = (SOURCE_ROOT / "target" / "package.py").read_text(encoding="utf-8")
    assert "ApplicationPackage" in package_source
    assert "package.to_files" in package_source
    assert "render_schema" not in package_source
    assert "render_services" not in package_source


def test_no_legacy_manifest_migration_functions_exist():
    manifest_source = (SOURCE_ROOT / "manifest.py").read_text(encoding="utf-8")
    assert "migrate_v" not in manifest_source
    assert "legacy" not in manifest_source.lower()
