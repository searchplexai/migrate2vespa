from __future__ import annotations

import shutil
from pathlib import Path

from migrate2vespa.cli import main


def test_primary_command_generates_next_to_project(tmp_path, capsys):
    source = Path(__file__).parents[1] / "fixtures" / "quickstart"
    project = tmp_path / "quickstart"
    shutil.copytree(source, project)

    result = main([str(project)])
    output = capsys.readouterr().out

    assert result == 0
    assert "Analysis: COMPLETE" in output
    assert "Vespa app generated successfully." in output
    assert "Vespa app: out/vespa-app" in output
    assert "Next: vespa deploy --wait 600 out/vespa-app" in output
    assert (project / "out" / "migration-manifest.yaml").is_file()


def test_analyze_only_writes_manifest_without_package(write_project, simple_mapping, capsys):
    project = write_project(simple_mapping)
    result = main([str(project), "--analyze-only"])
    output = capsys.readouterr().out
    assert result == 0
    assert "Vespa app generation not attempted." in output
    assert (project / "out" / "migration-manifest.yaml").is_file()
    assert not (project / "out" / "vespa-app").exists()


def test_advanced_generate_renders_existing_manifest(write_project, simple_mapping, capsys):
    project = write_project(simple_mapping)
    assert main([str(project), "--analyze-only"]) == 0
    capsys.readouterr()
    manifest = project / "out" / "migration-manifest.yaml"
    result = main(["generate", str(manifest)])
    output = capsys.readouterr().out
    assert result == 0
    assert "Vespa app generated successfully." in output


def test_invalid_input_returns_two(tmp_path, capsys):
    result = main([str(tmp_path / "missing")])
    output = capsys.readouterr().out
    assert result == 2
    assert "Analysis failed" in output


def test_safe_generation_block_returns_three(write_project, capsys):
    project = write_project({"mappings": {"properties": {
        "a-b": {"type": "keyword"}, "a_b": {"type": "keyword"}
    }}})
    result = main([str(project)])
    output = capsys.readouterr().out
    assert result == 3
    assert "Vespa app generation blocked." in output


def test_standalone_json_is_accepted(write_project, simple_mapping, capsys):
    project = write_project(simple_mapping)
    result = main([str(project / "mapping.json")])
    capsys.readouterr()
    assert result == 0
    assert (project / "out" / "vespa-app").is_dir()
