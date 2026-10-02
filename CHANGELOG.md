# Changelog

## 0.1.1 — 2026-10-02

- Hold back fields whose analyzer behavior the generated package cannot reproduce.
- Preserve `copy_to` source fields while omitting unpopulated destinations.
- Correct Boolean doc-values evidence and query diagnostics for held-back fields.
- Generate observed scalar-object child fields without reporting their containers as omissions.
- Handle `ignore_above` on keyword multi-fields with mixed scalar/array samples.
- Record `multi_match` field usage and support the narrow OpenSearch `knn_vector` form without a `method` definition.
- Allow reviewed manifests to omit fields or safely widen numeric target types.
- Preserve `index: false` when representative queries request text search; record the conflict on the field.
- Record the linguistic caveat for default text fields and accept explicit `element_type: float` vectors.
- Keep fields unsearchable when both `index` and `doc_values` are disabled, even when representative queries reference them.
- Clarify that Elasticsearch vector `index_options` are not translated.
- 0.1.0 cannot read 0.1.1 manifests that contain flattened-object fields;
  use 0.1.1 to read those manifests.

## 0.1.0 — 2026-08-26

Offline Elasticsearch/OpenSearch → Vespa schema planning.

- Assesses one local mapping with optional settings, documents and Query DSL.
- Writes a format-v1 `migration-manifest.yaml` with full artifact coverage.
- Recognizes core field types, multi-fields, arrays, lowercase normalizers,
  dates and float dense vectors; generates a Vespa package via PyVespa when safe.
- Records unrecognized constructs and omits unrecognized containers as subtrees.
- Pins the package version in automation; incompatible changes are listed here.
