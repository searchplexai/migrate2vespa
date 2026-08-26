# Security and trust boundary

migrate2vespa is offline-first.

## Runtime boundary

- Reads only paths explicitly supplied by the caller or referenced by `migration-manifest.yaml`.
- Writes beneath an explicitly supplied output directory or the documented `out/`
  directory beside the input (`migration-manifest.yaml` and, when safe,
  `vespa-app/`).
- Does not accept Elasticsearch/OpenSearch credentials.
- Does not include HTTP clients, telemetry, analytics, collectors, or cluster discovery.
- Does not deploy to Vespa or invoke Docker.
- Does not execute content found in mappings, settings, queries, or documents.

Installing Python dependencies may use the package installer’s configured network,
but running migrate2vespa does not require or initiate network access.

Generated files must still be reviewed before deployment. Source field names and
captured values are treated as untrusted data and are serialized rather than executed.
