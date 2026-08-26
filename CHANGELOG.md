# Changelog

## 0.1.0 — 2026-08-26

Offline Elasticsearch/OpenSearch → Vespa schema planning.

- Assesses one local mapping with optional settings, documents and Query DSL.
- Writes a format-v1 `migration-manifest.yaml` with full artifact coverage.
- Recognizes core field types, multi-fields, arrays, lowercase normalizers,
  dates and float dense vectors; generates a Vespa package via PyVespa when safe.
- Records unrecognized constructs and omits unrecognized containers as subtrees.
- Pins the package version in automation; incompatible changes are listed here.
