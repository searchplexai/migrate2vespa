# Overview

`migrate2vespa` does three things, and stops there.

| Stage | Who | Output |
|---|---|---|
| Assessment | this tool | `migration-manifest.yaml` |
| Generation | this tool | `vespa-app/` when safe |
| Live validation | you | deploy, feed, query |

Assessment infers target field requirements from your mapping, documents and
queries. It accounts for every supplied artifact. Unrecognized constructs are
recorded as `REVIEW` / `REDESIGN`, never invented.

Generation writes a Vespa application package from resolved target plans. Outcomes
are `READY`, `PARTIAL` (omissions recorded), or `BLOCKED` (nothing published).

Live validation is outside the tool. Deploy the package, feed the sample, and
check retrieval yourself. The fixture smoke path is
`scripts/validate-with-vespa-docker.sh`.

## Reading the manifest

Start with `coverage` — every supplied field, query and document line should be
accounted for. Then read each field top-down: `source` → `evidence` →
`required` / `normalized` → `decision` → `target`.

Each field and query has a decision:

- `DIRECT`: A straightforward Vespa representation is known.
- `ADAPT`: A known change is needed and recorded in the plan.
- `REVIEW`: Human review is needed before claiming equivalent behavior.
- `REDESIGN`: The source behavior needs a different design in Vespa.

Rule IDs live in `src/migrate2vespa/sources/elastic/rules.py`.

For query evidence, the tool recognizes `avg`, `cardinality`, `date_histogram`,
`histogram`, `max`, `min`, `range`, `stats`, `sum`, `terms`, and `value_count`
aggregations. It records their field usage; it does not translate them into
Vespa queries. Aggregation options require review.

`generation.omitted` lists what was left out of the package. `SUBTREE` omissions
cover a container and everything under it.
Scalar object containers represented by their child fields remain in artifact
coverage but are not counted as generated fields or omissions.

`generate <manifest.yaml>` can render a reviewed plan. Edits are checked
against the recorded requirements; for example, widening an integer target to
`long` is allowed, but an incompatible type change is rejected. Set a field's
`support.generate` to `false` to leave it out of the package while keeping it
in the manifest.
