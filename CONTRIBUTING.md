# Contributing

Reproduce patterns with synthetic mappings, settings, queries and documents.
Choose `DIRECT`, `ADAPT`, `REVIEW` or `REDESIGN`. Update the Pattern Registry
and add a regression test.

Do not contribute customer data. Refusal that is safer is as welcome as broader
translation.

## Boundaries

- Elastic parsing stays under `sources/elastic/`.
- Sources emit facts and capabilities; they do not build Vespa plans.
- `target/planner.py` owns Vespa field planning.
- Generation consumes resolved plans only.
- `manifest.py` owns the format-v1 contract.

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
```
