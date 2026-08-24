from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from .target import GenerationError
from .io import InputError, read_manifest
from .manifest import MigrationManifest
from .reporting import render_console_summary
from .workflow import analyze_input, generate_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate2vespa",
        description=(
            "Analyze local Elasticsearch/OpenSearch artifacts and, when safe, "
            "generate a Vespa starter package."
        ),
        epilog=(
            "Advanced: migrate2vespa generate <migration-manifest.yaml> renders "
            "an already-reviewed plan without rerunning mapping/query assessment."
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        help="migration directory or mapping JSON file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output directory (default: <input>/out)",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="write the migration manifest without generating a Vespa package",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed diagnostics")
    return parser


def build_generate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate2vespa generate",
        description=(
            "Render an already-reviewed migration manifest without rerunning "
            "mapping/query assessment."
        ),
    )
    parser.add_argument("manifest", type=Path, help="migration-manifest.yaml to render")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output directory (default: directory containing the manifest)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed diagnostics")
    return parser


def _default_output_for_source(source: Path) -> Path:
    source = source.resolve()
    return source / "out" if source.is_dir() else source.parent / "out"


def _summary_output_path(output_directory: Path, input_path: Path) -> Path:
    base = input_path.resolve() if input_path.resolve().is_dir() else input_path.resolve().parent
    try:
        relative_output = output_directory.relative_to(base)
    except ValueError:
        relative_output = output_directory
    return relative_output


def _print_summary(
    manifest: MigrationManifest,
    *,
    generation_status: str,
    output_directory: Path,
    input_path: Path,
    manifest_file: Path | None = None,
) -> None:
    relative_output = _summary_output_path(output_directory, input_path)
    next_step = None
    vespa_app_path = None
    if generation_status in {"READY", "PARTIAL"}:
        vespa_app_path = str(relative_output / "vespa-app")
        next_step = f"vespa deploy --wait 600 {shlex.quote(vespa_app_path)}"
    manifest_file = manifest_file or output_directory / "migration-manifest.yaml"
    manifest_parent = _summary_output_path(manifest_file.parent, input_path)
    displayed_manifest = manifest_parent / manifest_file.name
    print(
        render_console_summary(
            manifest,
            generation_status=generation_status,
            manifest_path=str(displayed_manifest),
            vespa_app_path=vespa_app_path,
            next_step=next_step,
        )
    )


def _run_generate(argv: list[str]) -> int:
    args = build_generate_parser().parse_args(argv)
    manifest_path = args.manifest.resolve()
    output_directory = (args.output or manifest_path.parent).resolve()
    try:
        if not manifest_path.is_file() or manifest_path.suffix.lower() not in {".yaml", ".yml"}:
            raise InputError("generate expects an existing migration-manifest.yaml file")
        manifest = read_manifest(manifest_path)
        generate_manifest(manifest_path, output_directory)
        manifest = read_manifest(manifest_path)
        _print_summary(
            manifest,
            generation_status=manifest.generation.status,
            output_directory=output_directory,
            input_path=manifest_path,
            manifest_file=manifest_path,
        )
        return 0
    except GenerationError as exc:
        if manifest_path.is_file():
            manifest = read_manifest(manifest_path)
        status = manifest.generation.status
        _print_summary(
            manifest,
            generation_status=status,
            output_directory=output_directory,
            input_path=manifest_path,
            manifest_file=manifest_path,
        )
        if args.verbose:
            print(f"\n{exc.code}: {exc}")
        return 3 if status == "BLOCKED" else 1
    except InputError as exc:
        print(f"{exc.code}: ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


def _run_default(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    output_directory = (args.output or _default_output_for_source(args.input)).resolve()
    try:
        manifest = analyze_input(args.input, output_directory)
        if args.analyze_only:
            _print_summary(
                manifest,
                generation_status="NOT ATTEMPTED",
                output_directory=output_directory,
                input_path=args.input,
            )
            if args.verbose:
                coverage = manifest.coverage
                print("\nCoverage:")
                print(f"  fields: {coverage.fields_assessed}/{coverage.fields_supplied} assessed")
                accounted_queries = coverage.queries_assessed + coverage.queries_invalid
                accounted_documents = coverage.documents_examined + coverage.documents_invalid
                print(
                    f"  queries: {accounted_queries}/{coverage.queries_supplied} "
                    "accounted for"
                )
                print(
                    f"  documents: {accounted_documents}/{coverage.documents_supplied} "
                    "accounted for"
                )
            return 0 if manifest.coverage.all_supplied_accounted_for else 1

        generate_manifest(output_directory / "migration-manifest.yaml", output_directory)
        manifest = read_manifest(output_directory / "migration-manifest.yaml")
        _print_summary(
            manifest,
            generation_status=manifest.generation.status,
            output_directory=output_directory,
            input_path=args.input,
        )
        return 0
    except GenerationError as exc:
        manifest_path = output_directory / "migration-manifest.yaml"
        if manifest_path.is_file():
            manifest = read_manifest(manifest_path)
        status = manifest.generation.status
        _print_summary(
            manifest,
            generation_status=status,
            output_directory=output_directory,
            input_path=args.input,
        )
        if args.verbose:
            print(f"\n{exc.code}: {exc}")
        return 3 if status == "BLOCKED" else 1
    except InputError as exc:
        print(f"{exc.code}: Analysis failed.\n\nERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "generate":
        return _run_generate(raw[1:])
    return _run_default(raw)


if __name__ == "__main__":
    raise SystemExit(main())
