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

Decisions: `DIRECT`, `ADAPT`, `REVIEW`, `REDESIGN`. Rule IDs live in
`src/migrate2vespa/sources/elastic/rules.py`.

`generation.omitted` lists what was left out of the package. `SUBTREE` omissions
cover a container and everything under it.
