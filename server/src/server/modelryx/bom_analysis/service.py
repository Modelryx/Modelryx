import hashlib
import json
from collections import Counter
from typing import Any

from modelryx.bom_analysis.models import (
    BOMDiff,
    BOMDiffEntry,
    BOMDuplicatePair,
    BOMDuplicateResponse,
    BOMNode,
    BOMQuery,
    BOMQueryResult,
    BOMReadinessResponse,
    BOMSnapshot,
    BOMSummary,
    CommonalityPair,
    CommonalityResponse,
    ReadinessFinding,
    ReadinessPolicy,
    StandardizationCandidate,
    StandardizationResponse,
)

STANDARDIZATION_RULES_VERSION = "phase7-standardization-v1"
STANDARDIZATION_WEIGHTS = {
    "geometry": 0.30,
    "metadata": 0.20,
    "interface": 0.20,
    "material": 0.10,
    "functional": 0.10,
    "usage": 0.10,
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


class BOMAnalysisService:
    """Analyze an already authorized BOM; this layer never queries around permissions."""

    @staticmethod
    def snapshot(
        bom: Any,
        drawing_revision_ids: set[Any] | None = None,
        metadata_by_revision: dict[Any, dict[str, Any]] | None = None,
    ) -> BOMSnapshot:
        drawing_revision_ids = drawing_revision_ids or set()
        metadata_by_revision = metadata_by_revision or {}
        nodes: list[BOMNode] = []

        def walk(
            rows: list[Any],
            prefix: str,
            ancestor_suppressed: bool = False,
            parent_quantity: int = 1,
        ) -> None:
            for row in rows:
                path = f"{prefix}/{row.occurrence_key}"
                suppressed = ancestor_suppressed or row.is_suppressed
                effective_quantity = parent_quantity * row.quantity
                metadata = metadata_by_revision.get(row.child_revision.id, {})
                nodes.append(
                    BOMNode(
                        occurrence_id=row.id,
                        occurrence_path=path,
                        item_id=row.child_part.id,
                        item_number=row.child_part.part_number,
                        item_name=row.child_part.name,
                        item_type=row.child_part.part_type,
                        revision_id=row.child_revision.id,
                        revision=row.child_revision.revision_code,
                        lifecycle_status=row.child_revision.state,
                        quantity=row.quantity,
                        effective_quantity=effective_quantity,
                        suppressed=suppressed,
                        has_drawing=row.child_revision.id in drawing_revision_ids,
                        material=metadata.get("material"),
                        mass_kg=metadata.get("mass_kg"),
                        metadata_status=metadata.get("status"),
                    )
                )
                walk(row.children, path, suppressed, effective_quantity)

        walk(bom.occurrences, f"/{bom.assembly_part.part_number}")
        content = {
            "root_revision_id": str(bom.assembly_revision.id),
            "nodes": [node.model_dump(mode="json") for node in nodes],
        }
        bom_hash = _hash(content)
        return BOMSnapshot(
            id=f"boms_{bom_hash[:24]}",
            root_item_id=bom.assembly_part.id,
            root_revision_id=bom.assembly_revision.id,
            root_item_number=bom.assembly_part.part_number,
            root_revision=bom.assembly_revision.revision_code,
            bom_hash=bom_hash,
            nodes=nodes,
        )

    @staticmethod
    def visible_nodes(snapshot: BOMSnapshot, include_suppressed: bool = False) -> list[BOMNode]:
        return [node for node in snapshot.nodes if include_suppressed or not node.suppressed]

    @staticmethod
    def visible_component(snapshot: BOMSnapshot, revision_id: Any) -> BOMNode | None:
        return next(
            (
                node
                for node in BOMAnalysisService.visible_nodes(snapshot)
                if node.revision_id == revision_id
            ),
            None,
        )

    def summary(self, snapshot: BOMSnapshot) -> BOMSummary:
        nodes = self.visible_nodes(snapshot)
        unique = {node.revision_id: node for node in nodes}
        lifecycle = Counter(node.lifecycle_status for node in unique.values())
        with_drawing = sum(node.has_drawing for node in unique.values())
        unique_count = len(unique)
        usage = Counter(node.revision_id for node in nodes)
        parts = [node for node in nodes if node.item_type == "PART"]
        material_quantities = Counter(
            {
                material: sum(
                    node.effective_quantity for node in parts if node.material == material
                )
                for material in sorted({node.material for node in parts if node.material})
            }
        )
        material_covered = sum(node.effective_quantity for node in parts if node.material)
        mass_covered = sum(node.effective_quantity for node in parts if node.mass_kg is not None)
        part_quantity = sum(node.effective_quantity for node in parts)
        return BOMSummary(
            snapshot_id=snapshot.id,
            unique_component_revisions=unique_count,
            total_occurrences=len(nodes),
            total_quantity=sum(node.effective_quantity for node in nodes),
            subassemblies=sum(node.item_type == "ASSEMBLY" for node in unique.values()),
            lifecycle=dict(sorted(lifecycle.items())),
            documentation={
                "with_drawing": with_drawing,
                "without_drawing": unique_count - with_drawing,
                "coverage_percent": round(with_drawing / unique_count * 100, 1)
                if unique_count
                else 100.0,
            },
            reuse={
                "reused_components": sum(count > 1 for count in usage.values()),
                "single_use_components": sum(count == 1 for count in usage.values()),
            },
            materials=dict(material_quantities),
            material_coverage={
                "covered_quantity": material_covered,
                "missing_quantity": part_quantity - material_covered,
                "coverage_percent": round(material_covered / part_quantity * 100, 1)
                if part_quantity
                else 100.0,
            },
            mass={
                "total_mass_kg": round(
                    sum(
                        float(node.mass_kg) * node.effective_quantity
                        for node in parts
                        if node.mass_kg is not None
                    ),
                    9,
                ),
                "covered_quantity": mass_covered,
                "missing_quantity": part_quantity - mass_covered,
                "coverage_percent": round(mass_covered / part_quantity * 100, 1)
                if part_quantity
                else 100.0,
            },
        )

    def execute(self, snapshot: BOMSnapshot, query: BOMQuery) -> BOMQueryResult:
        nodes = self.visible_nodes(snapshot, query.traversal.include_suppressed)
        if query.traversal.depth == "direct":
            root_depth = min((node.occurrence_path.count("/") for node in nodes), default=0)
            nodes = [node for node in nodes if node.occurrence_path.count("/") == root_depth]
        filters = query.filters
        if filters.lifecycle_status:
            wanted = {value.casefold() for value in filters.lifecycle_status}
            nodes = [node for node in nodes if node.lifecycle_status.casefold() in wanted]
        if filters.has_drawing is not None:
            nodes = [node for node in nodes if node.has_drawing is filters.has_drawing]
        if filters.item_type:
            nodes = [node for node in nodes if node.item_type in filters.item_type]
        if query.operation == "documentation_gaps":
            nodes = [node for node in nodes if not node.has_drawing]
        if query.operation == "single_use":
            counts = Counter(node.revision_id for node in nodes)
            nodes = [node for node in nodes if counts[node.revision_id] == 1]
        if query.traversal.mode == "unique_components":
            nodes = list({node.revision_id: node for node in nodes}.values())
        nodes.sort(key=lambda node: (node.item_number, node.revision, node.occurrence_path))
        total = len(nodes)
        return BOMQueryResult(
            snapshot_id=snapshot.id,
            operation=query.operation,
            total=total,
            returned=min(total, query.limit),
            truncated=total > query.limit,
            nodes=nodes[: query.limit],
            summary=self.summary(snapshot),
        )

    @staticmethod
    def plan(message: str) -> tuple[BOMQuery, str]:
        text = message.casefold()
        if "drawing" in text or "document" in text:
            return BOMQuery(operation="documentation_gaps"), "components without drawings"
        if "only once" in text or "single-use" in text or "single use" in text:
            return BOMQuery(operation="single_use"), "one occurrence in this assembly"
        lifecycle = next(
            (
                state
                for state in (
                    "DRAFT",
                    "UPLOADING",
                    "VALIDATING",
                    "COMMITTED",
                    "PROCESSING",
                    "READY",
                    "READY_WITH_WARNINGS",
                    "FAILED",
                )
                if state.casefold().replace("_", " ") in text
            ),
            None,
        )
        if lifecycle:
            return (
                BOMQuery(operation="lifecycle", filters={"lifecycle_status": [lifecycle]}),
                f"components in {lifecycle} lifecycle state",
            )
        if any(word in text for word in ("summary", "overview", "how many", "count")):
            return BOMQuery(operation="summary"), "BOM summary"
        return BOMQuery(operation="search"), "all readable components in this assembly"

    @staticmethod
    def compare(before: BOMSnapshot, after: BOMSnapshot) -> BOMDiff:
        def aggregate(snapshot: BOMSnapshot) -> dict[str, dict[str, Any]]:
            values: dict[str, dict[str, Any]] = {}
            for node in BOMAnalysisService.visible_nodes(snapshot):
                identity = node.item_number
                entry = values.setdefault(identity, {"node": node, "quantity": 0})
                entry["quantity"] += node.effective_quantity
            return values

        old, new = aggregate(before), aggregate(after)
        changes: list[BOMDiffEntry] = []
        for identity in sorted(set(old) | set(new)):
            left, right = old.get(identity), new.get(identity)
            if left is None:
                node = right["node"]
                changes.append(
                    BOMDiffEntry(
                        change="ADDED",
                        identity=identity,
                        item_number=identity,
                        item_id=node.item_id,
                        new_revision_id=node.revision_id,
                        new_revision=node.revision,
                        new_quantity=right["quantity"],
                    )
                )
            elif right is None:
                node = left["node"]
                changes.append(
                    BOMDiffEntry(
                        change="REMOVED",
                        identity=identity,
                        item_number=identity,
                        item_id=node.item_id,
                        old_revision_id=node.revision_id,
                        old_revision=node.revision,
                        old_quantity=left["quantity"],
                    )
                )
            elif left["node"].revision_id != right["node"].revision_id:
                changes.append(
                    BOMDiffEntry(
                        change="REVISION_CHANGED",
                        identity=identity,
                        item_number=identity,
                        item_id=right["node"].item_id,
                        old_revision_id=left["node"].revision_id,
                        new_revision_id=right["node"].revision_id,
                        old_revision=left["node"].revision,
                        new_revision=right["node"].revision,
                        old_quantity=left["quantity"],
                        new_quantity=right["quantity"],
                        comparison_path=(
                            f"/items/{right['node'].item_id}/compare"
                            f"?from={left['node'].revision_id}&to={right['node'].revision_id}"
                        ),
                    )
                )
            elif left["quantity"] != right["quantity"]:
                changes.append(
                    BOMDiffEntry(
                        change="QUANTITY_CHANGED",
                        identity=identity,
                        item_number=identity,
                        item_id=right["node"].item_id,
                        old_revision_id=left["node"].revision_id,
                        new_revision_id=right["node"].revision_id,
                        old_revision=left["node"].revision,
                        new_revision=right["node"].revision,
                        old_quantity=left["quantity"],
                        new_quantity=right["quantity"],
                    )
                )
        counts = Counter(change.change for change in changes)
        return BOMDiff(
            from_snapshot_id=before.id,
            to_snapshot_id=after.id,
            summary={
                key: counts[key]
                for key in ("ADDED", "REMOVED", "REVISION_CHANGED", "QUANTITY_CHANGED")
            },
            changes=changes,
        )

    @staticmethod
    def standardization_candidates(
        snapshot: BOMSnapshot,
        evidence: list[dict[str, Any]],
        *,
        minimum_score: float = 0.70,
        limit: int = 50,
    ) -> StandardizationResponse:
        nodes = BOMAnalysisService.visible_nodes(snapshot)
        by_revision = {node.revision_id: node for node in nodes if node.item_type == "PART"}
        usage = Counter(node.revision_id for node in nodes if node.item_type == "PART")
        candidates: dict[tuple[str, str], StandardizationCandidate] = {}
        for row in evidence:
            source_id = row["source_revision_id"]
            candidate_id = row["candidate_revision_id"]
            if source_id not in by_revision or candidate_id not in by_revision:
                continue
            key = tuple(sorted((str(source_id), str(candidate_id))))
            if key in candidates or row["classification"] in {"different", "insufficient_data"}:
                continue
            scores = row.get("scores") or {}
            signals: dict[str, float | None] = {
                "geometry": scores.get("geometry"),
                "metadata": scores.get("metadata"),
                "interface": None,
                "material": scores.get("material"),
                "functional": scores.get("identity"),
                "usage": ((1 / usage[source_id]) + (1 / usage[candidate_id])) / 2,
            }
            available = {name: float(value) for name, value in signals.items() if value is not None}
            used_weight = sum(STANDARDIZATION_WEIGHTS[name] for name in available)
            if not used_weight or signals["geometry"] is None or signals["metadata"] is None:
                continue
            score = (
                sum(value * STANDARDIZATION_WEIGHTS[name] for name, value in available.items())
                / used_weight
            )
            if score < minimum_score:
                continue
            missing = [name for name, value in signals.items() if value is None]
            left, right = by_revision[source_id], by_revision[candidate_id]
            candidates[key] = StandardizationCandidate(
                revision_a_id=source_id,
                revision_b_id=candidate_id,
                item_a=left.item_number,
                item_b=right.item_number,
                score=round(score, 6),
                score_coverage=round(used_weight, 6),
                classification=row["classification"],
                signals=signals,
                reasons=list(row.get("reasons") or []),
                differences=list(row.get("differences") or []),
                limitations=[
                    f"No authoritative {name} similarity evidence is available." for name in missing
                ]
                + [
                    "Tolerance, certification, load rating, and fit have not been "
                    "proven equivalent."
                ],
            )
        ordered = sorted(
            candidates.values(),
            key=lambda candidate: (-candidate.score, candidate.item_a, candidate.item_b),
        )
        return StandardizationResponse(
            snapshot_id=snapshot.id,
            rules_version=STANDARDIZATION_RULES_VERSION,
            total=len(ordered),
            returned=min(len(ordered), limit),
            truncated=len(ordered) > limit,
            candidates=ordered[:limit],
        )

    @staticmethod
    def duplicate_pairs(
        snapshot: BOMSnapshot, evidence: list[dict[str, Any]]
    ) -> BOMDuplicateResponse:
        visible = {
            node.revision_id: node
            for node in BOMAnalysisService.visible_nodes(snapshot)
            if node.item_type == "PART"
        }
        pairs: dict[tuple[str, str], BOMDuplicatePair] = {}
        for row in evidence:
            source_id, candidate_id = row["source_revision_id"], row["candidate_revision_id"]
            if source_id not in visible or candidate_id not in visible:
                continue
            if row["classification"] in {"different", "insufficient_data"}:
                continue
            key = tuple(sorted((str(source_id), str(candidate_id))))
            if key in pairs:
                continue
            pairs[key] = BOMDuplicatePair(
                revision_a_id=source_id,
                revision_b_id=candidate_id,
                item_a=visible[source_id].item_number,
                item_b=visible[candidate_id].item_number,
                classification=row["classification"],
                score=(row.get("scores") or {}).get("combined"),
                reasons=list(row.get("reasons") or []),
                differences=list(row.get("differences") or []),
            )
        ordered = sorted(
            pairs.values(),
            key=lambda pair: (-(pair.score or 0), pair.item_a, pair.item_b),
        )
        return BOMDuplicateResponse(snapshot_id=snapshot.id, total=len(ordered), pairs=ordered)

    @staticmethod
    def readiness(snapshot: BOMSnapshot, policy: ReadinessPolicy) -> BOMReadinessResponse:
        nodes = BOMAnalysisService.visible_nodes(snapshot)
        allowed = {state.casefold() for state in policy.allowed_lifecycle_states}
        rules: list[tuple[str, str, list[BOMNode], str]] = [
            (
                "LIFECYCLE_NOT_ALLOWED",
                "BLOCKING",
                [node for node in nodes if node.lifecycle_status.casefold() not in allowed],
                "components are outside the policy's allowed lifecycle states",
            )
        ]
        parts = [node for node in nodes if node.item_type == "PART"]
        if policy.require_drawings:
            rules.append(
                (
                    "DRAWING_REQUIRED",
                    "BLOCKING",
                    [node for node in parts if not node.has_drawing],
                    "parts are missing a required drawing",
                )
            )
        if policy.require_material:
            rules.append(
                (
                    "MATERIAL_REQUIRED",
                    "BLOCKING",
                    [node for node in parts if not node.material],
                    "parts are missing required material metadata",
                )
            )
        if policy.require_mass:
            rules.append(
                (
                    "MASS_REQUIRED",
                    "BLOCKING",
                    [node for node in parts if node.mass_kg is None],
                    "parts are missing required mass metadata",
                )
            )
        findings = [
            ReadinessFinding(
                code=code,
                severity=severity,
                count=len(matches),
                occurrence_ids=[node.occurrence_id for node in matches[:500]],
                message=f"{len(matches)} {message}.",
            )
            for code, severity, matches, message in rules
            if matches
        ]
        return BOMReadinessResponse(
            snapshot_id=snapshot.id,
            policy=policy,
            status="BLOCKED" if any(row.severity == "BLOCKING" for row in findings) else "READY",
            findings=findings,
            limitations=[
                "Phase 6 staging validation runs are not revision-linked after promotion; "
                "component validation roll-up is unavailable."
            ],
        )

    @staticmethod
    def commonality(snapshots: list[BOMSnapshot]) -> CommonalityResponse:
        component_sets = [
            {
                node.item_number
                for node in BOMAnalysisService.visible_nodes(snapshot)
                if node.item_type == "PART"
            }
            for snapshot in snapshots
        ]
        pairs: list[CommonalityPair] = []
        for left_index, left in enumerate(snapshots):
            for right_index in range(left_index + 1, len(snapshots)):
                right = snapshots[right_index]
                shared = component_sets[left_index] & component_sets[right_index]
                union = component_sets[left_index] | component_sets[right_index]
                pairs.append(
                    CommonalityPair(
                        revision_a_id=left.root_revision_id,
                        revision_b_id=right.root_revision_id,
                        shared_components=len(shared),
                        union_components=len(union),
                        commonality_percent=round(len(shared) / len(union) * 100, 1)
                        if union
                        else 100.0,
                        shared_item_numbers=sorted(shared),
                    )
                )
        overall_union = set().union(*component_sets)
        overall_shared = set.intersection(*component_sets) if component_sets else set()
        return CommonalityResponse(
            product_count=len(snapshots),
            overall_shared_components=len(overall_shared),
            overall_union_components=len(overall_union),
            overall_commonality_percent=round(len(overall_shared) / len(overall_union) * 100, 1)
            if overall_union
            else 100.0,
            overall_shared_item_numbers=sorted(overall_shared),
            pairs=pairs,
        )
