# Contributing

migrate2vespa accepts narrowly scoped migration patterns backed by synthetic evidence.

## Adding or changing a rule

1. Reproduce the source behavior using entirely synthetic mappings, settings,
   representative queries, and documents as applicable.
2. Decide whether the behavior is DIRECT, ADAPT, REVIEW, or REDESIGN. Unknown
   constructs use `REVIEW` with detection/generation support recorded separately.
3. Add or update stable metadata in the Pattern Registry.
4. Add a regression test that proves recognition, decision, and generation behavior.
5. Update the support table and explain any compatibility impact.

Do not contribute customer mappings, queries, documents, result sets, names, or
business logic. Generalize the underlying engineering pattern independently.

Contributions should make refusal safer as readily as they make translation broader.
An unsupported pattern becoming an explicit REVIEW or REDESIGN is useful progress.

## Architecture boundaries

- Elasticsearch/OpenSearch parsing and semantics stay under
  `src/migrate2vespa/sources/elastic/` and behind `ElasticSource`.
- Source code emits evidence, logical facts, and required capabilities; it must not
  construct Vespa target plans.
- `src/migrate2vespa/target/planner.py` owns source-independent Vespa field planning.
- Generation consumes resolved target plans and must not reinterpret source mappings.
- `src/migrate2vespa/manifest.py` defines the format-v1 manifest contract.

Run the suite with:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
```
