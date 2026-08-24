"""Stable Elasticsearch/OpenSearch rules shipped in migrate2vespa v0.1."""

from __future__ import annotations

from typing import Any

from ...manifest import Claim, Decision, EvidenceLevel, RequiredCapability
from ..rules import PatternRule


def _rule(
    rule_id: str,
    title: str,
    category: str,
    recognizes: str,
    decision: Decision,
    rationale: str,
    generate_supported: bool,
    fixture: str,
    capabilities: tuple[RequiredCapability, ...] = (),
    limitations: tuple[str, ...] = (),
    *,
    suppresses: tuple[RequiredCapability, ...] = (),
    surface: str = "FIELD_MODEL",
) -> PatternRule:
    return PatternRule(
        id=rule_id,
        title=title,
        category=category,
        recognizes=recognizes,
        decision=decision,
        rationale=rationale,
        generate_supported=generate_supported,
        limitations=limitations,
        fixture=fixture,
        capabilities=capabilities,
        suppresses_capabilities=suppresses,
        surface=surface,
    )


_RULES = (
    _rule(
        "ES-FIELD-TEXT-001", "Text field", "field", "text fields",
        Decision.DIRECT,
        "A Vespa indexed string preserves the basic lexical-search intent.",
        True, "quickstart/title", (RequiredCapability.TEXT_MATCH,),
        ("Exact analyzer parity is not claimed.",),
    ),
    _rule(
        "ES-FIELD-KEYWORD-001", "Keyword field", "field", "keyword fields",
        Decision.DIRECT,
        "A Vespa string attribute supports exact matching and filtering.",
        True, "quickstart/sku",
        (RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER),
        ("Referenced normalizers are assessed separately.",),
    ),
    _rule(
        "ES-FIELD-NUMERIC-001", "Numeric field", "field",
        "integer, long, float and double fields", Decision.DIRECT,
        "The supported numeric domain maps directly to a Vespa scalar attribute.",
        True, "quickstart/price", (RequiredCapability.FILTER, RequiredCapability.RANGE),
    ),
    _rule(
        "ES-FIELD-BOOLEAN-001", "Boolean field", "field", "boolean fields",
        Decision.DIRECT, "A boolean maps directly to a Vespa bool attribute.",
        True, "quickstart/in_stock", (RequiredCapability.FILTER,),
    ),
    _rule(
        "ES-FIELD-DATE-001", "Date field", "field",
        "date fields with a supported declared format", Decision.ADAPT,
        "Supported date values are normalized to epoch-second Vespa longs.",
        True, "synthetic/date", (RequiredCapability.RANGE,),
        ("Unsupported or ambiguous formats are not generated.",),
    ),
    _rule(
        "ES-MULTIFIELD-001", "Multi-field", "field",
        "alternate fields declared under fields", Decision.ADAPT,
        "The alternate representation becomes a separate Vespa field populated from its parent value.",
        True, "quickstart/title.raw",
    ),
    _rule(
        "ES-CUSTOM-ANALYZER-001", "Custom analyzer", "analysis",
        "a text field referencing custom index or search analysis", Decision.DIRECT,
        "The indexed-string target is clear while linguistic equivalence remains unresolved.",
        True, "quickstart/description", surface="TEXT_ANALYSIS",
        limitations=("Human review is required before claiming equivalent linguistic behavior.",),
    ),
    _rule(
        "ES-LUCENE-ANALYZER-001", "Built-in Lucene analyzer", "analysis",
        "a supported built-in Lucene language analyzer", Decision.ADAPT,
        "The field can use Vespa Lucene Linguistics, with configuration review.",
        True, "synthetic/lucene-analyzer", surface="TEXT_ANALYSIS",
        limitations=("Analyzer behavior is not claimed equivalent until validated.",),
    ),
    _rule(
        "ES-FIELD-INDEX-DISABLED-001", "Index disabled", "mapping_parameter",
        "index:false", Decision.ADAPT,
        "The target plan must not invent source inverted-index capability.",
        True, "synthetic/index-disabled",
        suppresses=(RequiredCapability.TEXT_MATCH, RequiredCapability.ANN),
    ),
    _rule(
        "ES-FIELD-DOCVALUES-DISABLED-001", "Doc values disabled", "mapping_parameter",
        "doc_values:false", Decision.ADAPT,
        "Source sort and aggregation capability is absent unless workload evidence requires a target attribute.",
        True, "synthetic/doc-values-disabled",
        suppresses=(RequiredCapability.SORT, RequiredCapability.GROUP),
    ),
    _rule(
        "ES-FIELD-LOOKUP-DISABLED-001", "Field lookup disabled", "mapping_parameter",
        "index:false together with doc_values:false", Decision.ADAPT,
        "The source declaration exposes no lookup capability for this field.",
        True, "synthetic/lookup-disabled",
        suppresses=(
            RequiredCapability.TEXT_MATCH,
            RequiredCapability.EXACT_MATCH,
            RequiredCapability.FILTER,
            RequiredCapability.RANGE,
            RequiredCapability.SORT,
            RequiredCapability.GROUP,
            RequiredCapability.ANN,
        ),
    ),
    _rule(
        "ES-TEXT-FIELDDATA-ENABLED-001", "Text fielddata enabled", "mapping_parameter",
        "fielddata:true on text", Decision.REVIEW,
        "Analyzed-token access requires an explicit target semantics review.",
        True, "synthetic/text-fielddata", surface="TEXT_ANALYSIS",
    ),
    _rule(
        "ES-KEYWORD-NORMALIZER-001", "Keyword normalizer", "analysis",
        "a keyword field referencing a normalizer", Decision.ADAPT,
        "A verified lowercase-only normalizer can be reproduced during feed conversion.",
        True, "synthetic/lowercase-normalizer", surface="TEXT_ANALYSIS",
        limitations=("Other normalizer chains are not generated in v0.1.",),
    ),
    _rule(
        "ES-DENSE-VECTOR-001", "Dense vector field", "field",
        "dimensioned float dense_vector fields", Decision.ADAPT,
        "A dimensioned float vector maps to a Vespa dense tensor attribute.",
        True, "quickstart/embedding", (RequiredCapability.ANN,),
        ("Omitted source defaults remain explicit version-unverified assumptions.",),
    ),
    _rule(
        "ES-CARDINALITY-MULTI-001", "Observed array", "value_shape",
        "at least one array value", Decision.ADAPT,
        "Vespa cardinality is explicit, so an observed array requires an array field.",
        True, "quickstart/tags",
    ),
    _rule(
        "ES-CARDINALITY-MIXED-001", "Observed mixed cardinality", "value_shape",
        "both scalar and array values", Decision.ADAPT,
        "The target must accept multiple values because arrays occur in the supplied documents.",
        True, "synthetic/mixed-cardinality",
    ),
    _rule(
        "ES-USAGE-TEXT-SEARCH-001", "Text search usage", "usage",
        "match or match_phrase on analyzed text", Decision.DIRECT,
        "Observed lexical search requires a Vespa indexed text field.",
        True, "quickstart/catalog-search", (RequiredCapability.TEXT_MATCH,),
    ),
    _rule(
        "ES-USAGE-SINGLE-TOKEN-MATCH-001", "Single-token match usage", "usage",
        "match or match_phrase on keyword", Decision.ADAPT,
        "Keyword matching must retain single-token semantics.",
        True, "synthetic/keyword-match", (RequiredCapability.EXACT_MATCH,),
    ),
    _rule(
        "ES-USAGE-EXACT-FILTER-001", "Exact filter usage", "usage",
        "term or terms on an exact-capable field", Decision.DIRECT,
        "Observed exact filtering requires a target attribute.",
        True, "quickstart/exact-sku",
        (RequiredCapability.EXACT_MATCH, RequiredCapability.FILTER),
    ),
    _rule(
        "ES-USAGE-ANALYZED-TERM-001", "Term query on analyzed text", "usage",
        "term or terms on text", Decision.REVIEW,
        "An indexed token is not equivalent to whole-value exact matching.",
        True, "synthetic/analyzed-term", (RequiredCapability.TEXT_MATCH,),
    ),
    _rule(
        "ES-USAGE-RANGE-001", "Range usage", "usage", "range query",
        Decision.DIRECT, "Observed range filtering requires an attribute-backed comparable value.",
        True, "synthetic/range", (RequiredCapability.RANGE, RequiredCapability.FILTER),
    ),
    _rule(
        "ES-USAGE-SORT-001", "Sort usage", "usage", "top-level sort",
        Decision.DIRECT, "Observed sorting requires a target attribute.",
        True, "synthetic/sort", (RequiredCapability.SORT,),
    ),
    _rule(
        "ES-USAGE-AGGREGATE-001", "Aggregation usage", "usage", "basic aggregation",
        Decision.ADAPT, "Observed grouping or metric aggregation requires an attribute-backed field.",
        True, "synthetic/aggregation", (RequiredCapability.GROUP,),
    ),
    _rule(
        "ES-USAGE-ANN-001", "ANN usage", "usage", "knn query",
        Decision.ADAPT, "Observed ANN retrieval requires an indexed dense tensor.",
        True, "quickstart/vector", (RequiredCapability.ANN,),
    ),
    _rule(
        "ES-UNSUPPORTED-001", "Unsupported source construct", "field",
        "a field type or mapping semantic outside the v0.1 registry", Decision.REVIEW,
        "No safe v0.1 rule can construct a target field without inventing semantics.",
        False, "synthetic/unsupported",
    ),
)

PATTERN_REGISTRY: dict[str, PatternRule] = {rule.id: rule for rule in _RULES}

CARDINALITY_RULES = {
    "MULTI_OBSERVED": "ES-CARDINALITY-MULTI-001",
    "MIXED_OBSERVED": "ES-CARDINALITY-MIXED-001",
}

USAGE_RULES = {
    "text_search": "ES-USAGE-TEXT-SEARCH-001",
    "single_token_match": "ES-USAGE-SINGLE-TOKEN-MATCH-001",
    "exact_filter": "ES-USAGE-EXACT-FILTER-001",
    "analyzed_term": "ES-USAGE-ANALYZED-TERM-001",
    "range_filter": "ES-USAGE-RANGE-001",
    "sort": "ES-USAGE-SORT-001",
    "aggregate": "ES-USAGE-AGGREGATE-001",
    "vector_retrieval": "ES-USAGE-ANN-001",
}


def effective_field_semantics(
    source_type: str,
    properties: dict[str, Any],
    rule_id: str,
) -> dict[str, Claim]:
    """Return only semantics needed by the v0.1 Vespa planner."""
    source = f"{rule_id}:common-elasticsearch-opensearch-semantics"
    keyword = source_type == "keyword"
    text = source_type == "text"
    numeric = source_type in {"integer", "long", "float", "double"}
    date = source_type == "date"
    vector = source_type == "dense_vector"
    doc_values_default = keyword or numeric or date
    indexed = properties.get("index", True)
    doc_values = properties.get("doc_values", doc_values_default)
    return {
        "indexed": Claim(
            value=indexed,
            evidence_level=(EvidenceLevel.DECLARED if "index" in properties else EvidenceLevel.INFERRED),
            source=(f"mapping.{source_type}.index" if "index" in properties else source),
            rule=rule_id,
        ),
        "doc_values": Claim(
            value=doc_values,
            evidence_level=(EvidenceLevel.DECLARED if "doc_values" in properties else EvidenceLevel.INFERRED),
            source=(f"mapping.{source_type}.doc_values" if "doc_values" in properties else source),
            rule=rule_id,
        ),
        "analyzed": Claim(value=text, evidence_level=EvidenceLevel.INFERRED, source=source, rule=rule_id),
        "exact_term_match": Claim(value=keyword, evidence_level=EvidenceLevel.INFERRED, source=source, rule=rule_id),
        "sortable": Claim(value=bool(doc_values), evidence_level=EvidenceLevel.INFERRED, source=source, rule=rule_id),
        "aggregatable": Claim(value=bool(doc_values), evidence_level=EvidenceLevel.INFERRED, source=source, rule=rule_id),
        "vector_retrieval": Claim(value=vector and indexed is not False, evidence_level=EvidenceLevel.INFERRED, source=source, rule=rule_id),
    }
