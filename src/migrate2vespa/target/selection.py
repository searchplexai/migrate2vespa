from __future__ import annotations

import re

from ..manifest import (
    Decision,
    FieldAssessment,
    GenerationOmission,
    GenerationPlan,
    GenerationScope,
    MigrationManifest,
)


def build_generation_plan(
    manifest: MigrationManifest,
    *,
    status: str = "NOT_RUN",
) -> GenerationPlan:
    """Resolve safe field/subtree omissions without interpreting source syntax."""
    fields = sorted(manifest.fields, key=lambda item: (item.source_name.count("."), item.source_name))
    omitted: list[GenerationOmission] = []
    covered: set[str] = set()
    package_blockers: list[str] = []

    for field in fields:
        if field.support.generate and field.target_plan is not None:
            continue
        if field.generation_scope is GenerationScope.SUBTREE:
            root = field.source_name
            if any(root == path or root.startswith(path + ".") for path in covered):
                continue
            covers = tuple(
                candidate.source_name
                for candidate in fields
                if candidate.source_name == root or candidate.source_name.startswith(root + ".")
            )
            covered.update(covers)
            omitted.append(_omission(field, covers=covers))
        elif field.generation_scope is GenerationScope.PACKAGE:
            covered.add(field.source_name)
            omitted.append(_omission(field))
            package_blockers.append(_reason(field))
        elif field.source_name not in covered:
            covered.add(field.source_name)
            omitted.append(_omission(field))

    # Close dependencies expressed by resolved target plans. A target field whose
    # source path belongs to an omitted unit cannot remain in the package or feed.
    changed = True
    while changed:
        changed = False
        for field in fields:
            if field.source_name in covered or field.target_plan is None:
                continue
            dependency = field.target_plan.source_path
            if any(dependency == path or dependency.startswith(path + ".") for path in covered):
                covered.add(field.source_name)
                omitted.append(
                    GenerationOmission(
                        source_path=field.source_name,
                        scope=GenerationScope.FIELD,
                        decision=Decision.REVIEW,
                        rules=tuple(field.rules),
                        reason=f"Depends on omitted source path {dependency}",
                    )
                )
                changed = True

    retained = [
        field
        for field in fields
        if field.source_name not in covered
        and field.support.generate
        and field.target_plan is not None
    ]
    target_names: dict[str, list[str]] = {}
    for field in retained:
        target_names.setdefault(field.target_plan.name, []).append(field.source_name)
    for target_name, source_names in target_names.items():
        if len(source_names) > 1:
            package_blockers.append(
                f"Target field name {target_name} is ambiguous for: {', '.join(source_names)}"
            )

    field_blocker_pattern = re.compile(r"^Field ([^:]+):")
    for blocker in manifest.blockers:
        match = field_blocker_pattern.match(blocker)
        if match and match.group(1) in covered:
            continue
        package_blockers.append(blocker)

    if not retained:
        package_blockers.append("Dependency closure leaves no source fields to generate")

    package_blockers = list(dict.fromkeys(package_blockers))
    planned_outcome = "BLOCKED" if package_blockers else "PARTIAL" if omitted else "READY"
    return GenerationPlan(
        status=status,
        planned_outcome=planned_outcome,
        generated_field_count=len(retained),
        supplied_field_count=len(fields),
        omitted=omitted,
        package_blockers=package_blockers,
    )


def retained_fields(
    manifest: MigrationManifest,
    plan: GenerationPlan | None = None,
) -> list[FieldAssessment]:
    plan = plan or build_generation_plan(manifest)
    omitted = {
        covered
        for item in plan.omitted
        for covered in (item.covers or (item.source_path,))
    }
    return [
        field
        for field in manifest.fields
        if field.source_name not in omitted
        and field.support.generate
        and field.target_plan is not None
    ]


def _omission(
    field: FieldAssessment,
    *,
    covers: tuple[str, ...] = (),
) -> GenerationOmission:
    return GenerationOmission(
        source_path=field.source_name,
        scope=field.generation_scope,
        decision=field.decision,
        rules=tuple(field.rules),
        reason=_reason(field),
        covers=covers,
    )


def _reason(field: FieldAssessment) -> str:
    if field.reasons:
        return field.reasons[0]
    return "No safe resolved Vespa target plan"
