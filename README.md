# migrate2vespa

A workload-aware schema migration planner for Vespa, currently supporting
Elasticsearch and OpenSearch.

> Don't translate field types. Infer field requirements.

Reads local source artifacts, accounts for every supplied field, document and
representative query, and writes a reviewable migration manifest. When safe, it
also generates a Vespa application package with PyVespa.

Source-specific interpretation is isolated from the migration manifest, Vespa
planner and package generator. Additional source engines can therefore be added
without duplicating the Vespa planning pipeline.

Offline and deterministic. No cluster connections, no LLM, no behavioral-parity
claim. Built by [Searchplex](https://www.searchplex.net/).

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

Next step printed on success: `vespa deploy --wait 600 out/vespa-app`.

## Input

```text
es-app/
├── mapping.json          # required
├── settings.json         # optional analysis settings
├── documents.jsonl       # optional representative documents
└── queries/              # optional _search request bodies
```

Canonical names preferred; a single unambiguous `*mapping*.json` /
`*setting*.json` is accepted. Documents may use `_id`/`_source` or an `id`
field. Queries supply usage evidence; they are not compiled to YQL. One source
index per run. `migrate2vespa ./mapping.json` is mapping-only.

## Output

```text
es-app/out/
├── migration-manifest.yaml
└── vespa-app/            # absent when generation is blocked or skipped
    ├── services.xml
    ├── schemas/<schema>.sd
    └── feed/documents.jsonl
```

The manifest records source semantics, evidence, capabilities, proposed Vespa
fields, decisions, risks and rule IDs, plus a coverage ledger. Generation is
`READY`, `PARTIAL` (omissions recorded; containers omitted with their children),
or `BLOCKED`. See [docs/overview.md](docs/overview.md).

## Recognized surface

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

Exit codes: `0` success, `2` invalid input, `3` blocked generation, `1` other
failure. Status vocabulary: `READY`, `READY WITH CAVEATS`, `DECISION REQUIRED`,
`REDESIGN REQUIRED`.

## Trust boundary

Reads only paths you supply. Writes under the chosen output directory. No
credentials, telemetry or network clients at runtime. Review and validate the
generated package before production use — [SECURITY.md](SECURITY.md).

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
scripts/validate-with-vespa-docker.sh   # needs Docker + Vespa CLI
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

Copyright © 2026 Searchplex. Licensed under the Apache License 2.0.
