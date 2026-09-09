# migrate2vespa

A schema migration planner for Vespa, currently supporting Elasticsearch and
OpenSearch.

Given a mapping—and optionally settings, sample documents, and representative
queries—it:

1. Creates a migration manifest showing how each field and configuration maps
   to Vespa, with every decision classified as `DIRECT`, `ADAPT`, `REVIEW`, or
   `REDESIGN`.
2. Generates a starter Vespa application for the parts of the schema that can be
   converted safely.

The tool works entirely from local files. It makes no network connections or
LLM calls.

Built by [Searchplex](https://www.searchplex.net/).

## Quickstart

Requires Python 3.11–3.13.

```bash
python3 -m pip install migrate2vespa
migrate2vespa ./es-app
```

Results go to `./es-app/out/` by default. Try the fixture:

```bash
git clone https://github.com/searchplexai/migrate2vespa.git
cd migrate2vespa
python3 -m pip install -e .
migrate2vespa fixtures/quickstart
```

On success, the tool prints the command for deploying the generated app.

## Input

```text
es-app/
├── mapping.json          # required
├── settings.json         # optional analysis settings
├── documents.jsonl       # optional representative documents
└── queries/              # optional _search request bodies
```

Use the filenames shown above. Documents may use Elasticsearch's `_id` and
`_source` format or a plain `id` field.

Queries help the tool understand how fields are used. They are not converted to
YQL.

Run one index at a time. Passing `mapping.json` directly runs without settings,
documents, or queries.

## Output

```text
es-app/out/
├── migration-manifest.yaml
└── vespa-app/            # absent when generation is blocked or skipped
    ├── services.xml
    ├── schemas/<schema>.sd
    └── feed/documents.jsonl
```

The manifest explains what the tool found, what it converted, and what still
needs attention.

The Vespa app is generated when it is safe to do so. Unsupported fields may be
left out and listed in the manifest. If that would make the app unsafe,
generation stops.

See [docs/overview.md](docs/overview.md) for more detail.

## Supported

| Surface | Behavior |
|---|---|
| Fields | `text`, `keyword`, `boolean`, `integer`, `long`, `float`, `double`, recognized `date`, dimensioned float `dense_vector` |
| Adaptations | Multi-fields, observed arrays, lowercase normalizers, dates → epoch seconds, vectors → tensors |
| Query evidence | `match`, `match_phrase`, `term`, `terms`, `range`, sort, basic aggregations, `knn`; custom scoring → redesign |

Unrecognized constructs are recorded, not guessed.

## CLI

```text
migrate2vespa ./es-app --analyze-only
migrate2vespa ./es-app --output ./migration-work
migrate2vespa generate ./es-app/out/migration-manifest.yaml
```

Exit codes are `0` for success, `2` for invalid input, `3` when generation is
blocked, and `1` for an unexpected error.

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
scripts/validate-with-vespa-docker.sh   # needs Docker + Vespa CLI
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

Copyright © 2026 Searchplex. Licensed under the Apache License 2.0.
