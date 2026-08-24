from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..manifest import Decision, RequiredCapability, worst_decision


@dataclass(frozen=True)
class PatternRule:
    """Source-neutral description of one migration rule."""

    id: str
    title: str
    category: str
    recognizes: str
    decision: Decision
    rationale: str
    generate_supported: bool
    limitations: tuple[str, ...]
    fixture: str
    capabilities: tuple[RequiredCapability, ...] = ()
    suppresses_capabilities: tuple[RequiredCapability, ...] = ()
    references: tuple[str, ...] = ()
    surface: str = "FIELD_MODEL"

RuleRegistry = Mapping[str, PatternRule]


def registry_decision(registry: RuleRegistry, rule_ids: list[str]) -> Decision:
    """Return the minimum semantic severity required by matched rules."""
    return worst_decision(*(registry[rule_id].decision for rule_id in rule_ids))


def registry_capabilities(
    registry: RuleRegistry,
    rule_ids: list[str],
) -> list[RequiredCapability]:
    """Return the controlled target requirements established by matched rules."""
    rules = [registry[rule_id] for rule_id in rule_ids]
    declared_capabilities = {
        capability
        for rule in rules
        if rule.category != "usage"
        for capability in rule.capabilities
    }
    suppressed_capabilities = {
        capability
        for rule in rules
        for capability in rule.suppresses_capabilities
    }
    observed_capabilities = {
        capability
        for rule in rules
        if rule.category == "usage"
        for capability in rule.capabilities
    }
    capabilities = (
        declared_capabilities - suppressed_capabilities
    ) | observed_capabilities
    return sorted(
        capabilities,
        key=lambda capability: list(RequiredCapability).index(capability),
    )


def registry_can_generate(registry: RuleRegistry, rule_ids: list[str]) -> bool:
    """Return whether every matched semantic can participate in generation."""
    return bool(rule_ids) and all(
        registry[rule_id].generate_supported for rule_id in rule_ids
    )
