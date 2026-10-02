# migrate2vespa

A schema migration planner for Vespa, currently supporting Elasticsearch and
OpenSearch.

Given a mapping and optional settings, sample documents, and representative
queries, it:

1. Creates a migration manifest showing how each field and configuration maps
   to Vespa, with every decision classified as `DIRECT`, `ADAPT`, `REVIEW`, or
   `REDESIGN` ([what these mean](docs/overview.md#reading-the-manifest)).
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

The fixture ends `PARTIAL` because `description` needs an analyzer decision.

On success, the tool prints the deploy command. If you supplied sample documents,
you can then feed the converted sample file. For `./es-app`, from its parent
directory:

```bash
vespa deploy --wait 600 ./es-app/out/vespa-app
vespa feed ./es-app/out/vespa-app/feed/documents.jsonl
```

## Input

```text
es-app/
├── mapping.json          # required
├── settings.json         # optional analysis settings
├── documents.jsonl       # optional representative documents
└── queries/              # optional _search request bodies
```

Save `GET /<index>/_mapping` as `mapping.json`; the single-index response wrapper
is accepted. Optionally save `GET /<index>/_settings` as `settings.json`, one
document per line in `documents.jsonl`, and individual `_search` request bodies
as `queries/*.json`. Documents may use `_id` and `_source` or a plain `id` field.

Queries help the tool understand how fields are used. They are not converted to
YQL.

Run one index at a time. Passing `mapping.json` directly runs without settings,
documents, or queries.
Without documents, ordinary fields use a scalar assumption and object trees are
omitted. Samples can establish arrays and scalar objects for generation.

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
needs attention. Field names may change, for example `title.raw` becomes
`title__raw`; `field_aliases` in the manifest lists the renames.

The generated feed contains converted sample documents, not a full export.

The Vespa app is generated when it is safe to do so. Unsupported fields may be
left out and listed in the manifest. If that would make the app unsafe,
generation stops.
A `PARTIAL` run also exits `0`; inspect omissions before deploying.

See [docs/overview.md](docs/overview.md) for more detail.

## Supported

| Surface | Behavior |
|---|---|
| Fields | `text`, `keyword`, `boolean`, `integer`, `long`, `float`, `double`, recognized `date`, float `dense_vector` without explicit `index_options`, and OpenSearch `knn_vector` without a `method` definition |
| Adaptations | Multi-fields, observed arrays and scalar objects, lowercase normalizers, supported dates and vectors; `ignore_above` on keyword multi-fields |
| Query evidence | `match`, `match_phrase`, `multi_match`, `term`, `terms`, `range`, sort, selected aggregations, `knn`; custom scoring → redesign |

- Dates: `strict_date_optional_time`, `epoch_millis`, and `epoch_second` are
  supported, including `||` combinations except one with both epoch units.
  Dates generate as epoch-second `long` fields, losing sub-second precision.
  Fields with other formats remain in the manifest but are not generated.
- Text analysis: Vespa's default linguistics can differ from Elasticsearch's
  standard analyzer. Custom and non-default built-in analyzers need review
  before their fields can generate.
- Lowercase normalizers: Applied to the generated sample feed, not configured
  as a Vespa normalizer.
- Vectors: OpenSearch `knn_vector` needs `index.knn: true`; OpenSearch `method`
  and Elasticsearch `index_options` definitions need review.
- `copy_to`: Source fields remain available, but the copied destination is left
  out until its behavior is designed.
- Unrecognized constructs: Recorded in the manifest, not guessed.

## CLI

Normal run:

```bash
migrate2vespa ./es-app
```

To review the manifest before generating:

```bash
migrate2vespa ./es-app --analyze-only
migrate2vespa generate ./es-app/out/migration-manifest.yaml
```

Use `--output ./migration-work` to write elsewhere.

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
