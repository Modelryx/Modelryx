import uuid
from types import SimpleNamespace

from modelryx.bom_analysis.models import BOMQuery, ReadinessPolicy
from modelryx.bom_analysis.service import BOMAnalysisService


def ns(**values):
    return SimpleNamespace(**values)


def occurrence(
    *,
    key: str,
    item_number: str,
    revision_id: uuid.UUID,
    revision: str = "A",
    quantity: int = 1,
    state: str = "READY",
    suppressed: bool = False,
    children: list | None = None,
):
    return ns(
        id=uuid.uuid4(),
        occurrence_key=key,
        child_part=ns(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, item_number),
            part_number=item_number,
            name=f"Component {item_number}",
            part_type="PART",
        ),
        child_revision=ns(
            id=revision_id,
            revision_code=revision,
            state=state,
        ),
        quantity=quantity,
        is_suppressed=suppressed,
        children=children or [],
    )


def bom(rows: list):
    root_item_id = uuid.uuid4()
    root_revision_id = uuid.uuid4()
    return ns(
        assembly_part=ns(id=root_item_id, part_number="PF-ROOT"),
        assembly_revision=ns(id=root_revision_id, revision_code="C"),
        assembly_version=ns(id=uuid.uuid4()),
        occurrences=rows,
    )


def test_summary_distinguishes_unique_occurrences_and_quantity() -> None:
    bearing = uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(key="left", item_number="PF-BEARING", revision_id=bearing),
                occurrence(
                    key="right",
                    item_number="PF-BEARING",
                    revision_id=bearing,
                    quantity=2,
                ),
                occurrence(
                    key="hidden",
                    item_number="PF-HIDDEN",
                    revision_id=uuid.uuid4(),
                    suppressed=True,
                ),
            ]
        )
    )
    summary = BOMAnalysisService().summary(snapshot)
    assert summary.unique_component_revisions == 1
    assert summary.total_occurrences == 2
    assert summary.total_quantity == 3
    assert summary.reuse == {"reused_components": 1, "single_use_components": 0}


def test_suppressed_parent_hides_descendants_and_comparison_ignores_them() -> None:
    hidden_child_revision = uuid.uuid4()
    suppressed_parent = occurrence(
        key="hidden-subassembly",
        item_number="PF-SUB",
        revision_id=uuid.uuid4(),
        suppressed=True,
        children=[
            occurrence(
                key="hidden-child",
                item_number="PF-HIDDEN-CHILD",
                revision_id=hidden_child_revision,
            )
        ],
    )
    suppressed_parent.child_part.part_type = "ASSEMBLY"
    before = BOMAnalysisService.snapshot(bom([suppressed_parent]))
    after_source = bom([])
    after_source.assembly_part = ns(id=before.root_item_id, part_number="PF-ROOT")
    after = BOMAnalysisService.snapshot(after_source)

    assert BOMAnalysisService().summary(before).total_occurrences == 0
    assert BOMAnalysisService.compare(before, after).changes == []
    assert all(node.suppressed for node in before.nodes)


def test_drawing_gap_and_single_use_queries_are_deterministic() -> None:
    reused = uuid.uuid4()
    unique = uuid.uuid4()
    source = bom(
        [
            occurrence(key="a", item_number="PF-A", revision_id=reused),
            occurrence(key="b", item_number="PF-A", revision_id=reused),
            occurrence(key="c", item_number="PF-C", revision_id=unique),
        ]
    )
    snapshot = BOMAnalysisService.snapshot(source, {reused})
    service = BOMAnalysisService()
    gaps = service.execute(snapshot, BOMQuery(operation="documentation_gaps"))
    singles = service.execute(snapshot, BOMQuery(operation="single_use"))
    assert [node.item_number for node in gaps.nodes] == ["PF-C"]
    assert [node.item_number for node in singles.nodes] == ["PF-C"]


def test_safe_planner_resolves_supported_intents_without_sql() -> None:
    drawing, meaning = BOMAnalysisService.plan("Which parts have no drawing?")
    single, scope = BOMAnalysisService.plan("Which parts are used only once?")
    lifecycle, _ = BOMAnalysisService.plan("Show failed components")
    assert drawing.operation == "documentation_gaps"
    assert single.operation == "single_use"
    assert scope == "one occurrence in this assembly"
    assert lifecycle.filters.lifecycle_status == ["FAILED"]
    assert "SQL" not in meaning


def test_snapshot_is_stable_and_diff_reports_revision_and_quantity_changes() -> None:
    old_revision = uuid.uuid4()
    new_revision = uuid.uuid4()
    before_bom = bom(
        [occurrence(key="part", item_number="PF-1", revision_id=old_revision, quantity=1)]
    )
    after_bom = ns(
        assembly_part=before_bom.assembly_part,
        assembly_revision=ns(id=uuid.uuid4(), revision_code="D"),
        assembly_version=ns(id=uuid.uuid4()),
        occurrences=[
            occurrence(
                key="part", item_number="PF-1", revision_id=new_revision, revision="B", quantity=2
            ),
            occurrence(key="added", item_number="PF-2", revision_id=uuid.uuid4()),
        ],
    )
    first = BOMAnalysisService.snapshot(before_bom)
    again = BOMAnalysisService.snapshot(before_bom)
    diff = BOMAnalysisService.compare(first, BOMAnalysisService.snapshot(after_bom))
    assert first.id == again.id
    assert diff.summary["REVISION_CHANGED"] == 1
    assert diff.summary["ADDED"] == 1
    changed = next(change for change in diff.changes if change.item_number == "PF-1")
    assert (changed.old_revision, changed.new_revision) == ("A", "B")
    assert (changed.old_quantity, changed.new_quantity) == (1, 2)
    assert str(old_revision) in changed.comparison_path
    assert str(new_revision) in changed.comparison_path


def test_nested_effective_quantity_material_and_mass_rollup_are_coverage_aware() -> None:
    bearing_revision = uuid.uuid4()
    gearbox_revision = uuid.uuid4()
    gearbox = occurrence(
        key="gearbox",
        item_number="PF-GEARBOX",
        revision_id=gearbox_revision,
        quantity=3,
        children=[
            occurrence(
                key="bearing",
                item_number="PF-BEARING",
                revision_id=bearing_revision,
                quantity=2,
            ),
            occurrence(
                key="unknown",
                item_number="PF-UNKNOWN",
                revision_id=uuid.uuid4(),
            ),
        ],
    )
    gearbox.child_part.part_type = "ASSEMBLY"
    snapshot = BOMAnalysisService.snapshot(
        bom([gearbox]),
        metadata_by_revision={
            bearing_revision: {"material": "bearing_steel", "mass_kg": 0.12, "status": "READY"}
        },
    )
    summary = BOMAnalysisService().summary(snapshot)
    bearing = next(node for node in snapshot.nodes if node.item_number == "PF-BEARING")

    assert bearing.quantity == 2
    assert bearing.effective_quantity == 6
    assert summary.materials == {"bearing_steel": 6}
    assert summary.material_coverage == {
        "covered_quantity": 6,
        "missing_quantity": 3,
        "coverage_percent": 66.7,
    }
    assert summary.mass == {
        "total_mass_kg": 0.72,
        "covered_quantity": 6,
        "missing_quantity": 3,
        "coverage_percent": 66.7,
    }


def test_standardization_candidates_are_scored_and_explicitly_advisory() -> None:
    left_revision = uuid.uuid4()
    right_revision = uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(key="left", item_number="PF-LEFT", revision_id=left_revision),
                occurrence(key="right", item_number="PF-RIGHT", revision_id=right_revision),
            ]
        )
    )
    response = BOMAnalysisService.standardization_candidates(
        snapshot,
        [
            {
                "source_revision_id": left_revision,
                "candidate_revision_id": right_revision,
                "classification": "similar",
                "scores": {
                    "geometry": 0.96,
                    "metadata": 0.92,
                    "material": 1.0,
                    "identity": 0.8,
                },
                "reasons": ["Geometry similarity is 96%."],
                "differences": ["Feature counts differ."],
            }
        ],
    )
    assert response.total == 1
    candidate = response.candidates[0]
    assert candidate.score > 0.9
    assert candidate.score_coverage == 0.8
    assert candidate.signals["interface"] is None
    assert "interchangeability has not been established" in candidate.advisory
    assert any("interface" in limitation for limitation in candidate.limitations)


def test_standardization_requires_geometry_and_metadata_evidence() -> None:
    left_revision = uuid.uuid4()
    right_revision = uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(key="left", item_number="PF-LEFT", revision_id=left_revision),
                occurrence(key="right", item_number="PF-RIGHT", revision_id=right_revision),
            ]
        )
    )
    response = BOMAnalysisService.standardization_candidates(
        snapshot,
        [
            {
                "source_revision_id": left_revision,
                "candidate_revision_id": right_revision,
                "classification": "similar",
                "scores": {"material": 1.0, "identity": 1.0},
            }
        ],
    )
    assert response.candidates == []


def test_duplicate_pairs_are_bom_scoped_symmetric_and_advisory() -> None:
    left_revision, right_revision = uuid.uuid4(), uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(key="left", item_number="PF-LEFT", revision_id=left_revision),
                occurrence(key="right", item_number="PF-RIGHT", revision_id=right_revision),
            ]
        )
    )
    evidence = [
        {
            "source_revision_id": left_revision,
            "candidate_revision_id": right_revision,
            "classification": "likely_duplicate",
            "scores": {"combined": 0.94},
            "reasons": ["Geometry similarity is 96%."],
            "differences": ["Materials differ."],
        },
        {
            "source_revision_id": right_revision,
            "candidate_revision_id": left_revision,
            "classification": "likely_duplicate",
            "scores": {"combined": 0.93},
        },
    ]
    result = BOMAnalysisService.duplicate_pairs(snapshot, evidence)
    assert result.total == 1
    assert result.pairs[0].score == 0.94
    assert "no BOM change" in result.pairs[0].advisory


def test_readiness_uses_explicit_policy_and_discloses_validation_gap() -> None:
    revision_id = uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom([occurrence(key="part", item_number="PF-WORK", revision_id=revision_id, state="DRAFT")])
    )
    result = BOMAnalysisService.readiness(
        snapshot,
        ReadinessPolicy(require_drawings=True, require_material=True, require_mass=True),
    )
    assert result.status == "BLOCKED"
    assert {finding.code for finding in result.findings} == {
        "LIFECYCLE_NOT_ALLOWED",
        "DRAWING_REQUIRED",
        "MATERIAL_REQUIRED",
        "MASS_REQUIRED",
    }
    assert result.validation_coverage == "UNAVAILABLE"
    assert "not revision-linked" in result.limitations[0]


def test_commonality_uses_declared_unique_item_number_jaccard_formula() -> None:
    def product(item_numbers: list[str]):
        return BOMAnalysisService.snapshot(
            bom(
                [
                    occurrence(key=value, item_number=value, revision_id=uuid.uuid4())
                    for value in item_numbers
                ]
            )
        )

    first = product(["PF-A", "PF-B", "PF-C"])
    second = product(["PF-B", "PF-C", "PF-D"])
    third = product(["PF-C", "PF-D", "PF-E"])
    result = BOMAnalysisService.commonality([first, second, third])

    assert result.formula == "shared unique item numbers / union unique item numbers"
    assert result.overall_shared_item_numbers == ["PF-C"]
    assert result.overall_commonality_percent == 20.0
    assert result.pairs[0].shared_item_numbers == ["PF-B", "PF-C"]
    assert result.pairs[0].commonality_percent == 50.0


def test_knowledge_target_must_be_a_visible_component_revision() -> None:
    visible_revision, suppressed_revision = uuid.uuid4(), uuid.uuid4()
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(key="visible", item_number="PF-VISIBLE", revision_id=visible_revision),
                occurrence(
                    key="suppressed",
                    item_number="PF-HIDDEN",
                    revision_id=suppressed_revision,
                    suppressed=True,
                ),
            ]
        )
    )
    assert BOMAnalysisService.visible_component(snapshot, visible_revision) is not None
    assert BOMAnalysisService.visible_component(snapshot, suppressed_revision) is None
    assert BOMAnalysisService.visible_component(snapshot, uuid.uuid4()) is None


def test_large_bom_is_aggregated_and_hard_bounded_before_interpretation() -> None:
    snapshot = BOMAnalysisService.snapshot(
        bom(
            [
                occurrence(
                    key=f"occ-{index}",
                    item_number=f"PF-LARGE-{index:04d}",
                    revision_id=uuid.uuid5(uuid.NAMESPACE_URL, f"phase7-large-{index}"),
                )
                for index in range(1200)
            ]
        )
    )
    result = BOMAnalysisService().execute(snapshot, BOMQuery(operation="search", limit=500))
    assert result.total == 1200
    assert result.returned == 500
    assert result.truncated is True
    assert len(result.nodes) == 500
