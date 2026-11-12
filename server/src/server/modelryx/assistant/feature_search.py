"""Authoritative assembly feature discovery and counting rules.

MAINTENANCE CONTRACT — keep both assistant entry points on these same rules:
1. Global discovery starts with readable part REVISIONS with positive feature
   evidence, then follows their parents recursively. Deduplicate result revisions,
   not occurrence paths. Never replace a BOM child with the Item's latest revision.
2. Revision answers traverse the committed BOM DOWNWARD. A part contributes its
   recorded count multiplied by every quantity along its occurrence path. Reused
   revisions cache reads, but each repeated occurrence contributes independently.
3. Suppressed branches are excluded; hidden, active occurrences still contribute.
4. Missing/stale/failed/unsupported evidence is unknown, NEVER zero. Partial positive
   counts are lower bounds. Complete zero counts alone establish absence. Known
   partial assembly structure also makes descendant totals incomplete.
5. Assembly-level feature counts stay separate: they may overlap component geometry
   and MUST NOT be added to component totals or counted again at each ancestor.
6. Authorize every revision before returning its identity/evidence or traversing it.
   Inaccessible branches make totals incomplete without exposing their identities.
7. Detect cycles per PATH (not per revision globally). Depth/work limits make results
   explicitly incomplete. Never present a bounded traversal as exhaustive.
8. Compute assembly totals downward even when discovered upward. Discovery paths
   explain matches; they are not a second source of quantities to add to totals.
9. Legacy worker-only snapshots may inherit a source BOM only across explicitly
   worker-created versions with identical, nonempty authoritative associations.
   Never fall back across a CAD commit just because an older BOM exists.

The BOM pins child revisions, not child content snapshots. For each referenced
revision we inspect its current committed source snapshot, as the existing contract
permits. This read-only service never changes references, versions, or engineering data.
"""

import re
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from modelryx.api.items import readable_item_revision
from modelryx.core.auth import Principal
from modelryx.engineering_search.interpreter import deterministic_interpret
from modelryx.engineering_search.models import AISearchRequest
from modelryx.engineering_search.registry import SEARCH_CAPABILITIES
from modelryx.models import (
    Artifact,
    BomLine,
    BomViewRevision,
    Item,
    MetadataExtraction,
    Revision,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
)
from modelryx.services.artifact_content import latest_content_snapshot_id

SUPPORTED_FEATURES = tuple(SEARCH_CAPABILITIES["fields"]["feature_filters"]["features"])
UNSUPPORTED_FEATURES = ("slot", "boss", "rib")
MAX_DEPTH = 20
MAX_OCCURRENCES = 1000
MAX_SEEDS = 1000
_QUESTION_WORDS = frozenset(
    """
    find search show me list all any which what where is are does do there a an the
    in on this these that those part parts assembly assemblies component components
    revision revisions contain contains containing with without no not has have how
    many count counts total totals number of and or please their parents parent for
    features feature recorded throughout entire whole recursively recursive include
    including use used free exists exist
""".split()
)


def requested_features(message: str) -> list[str]:
    return [
        feature
        for feature in (*SUPPORTED_FEATURES, *UNSUPPORTED_FEATURES)
        if re.search(rf"\b{feature}(?:{'es' if feature == 'boss' else 's'})?\b", message, re.I)
    ]


def validate_feature_question(message: str, *, discovery: bool = False) -> None:
    """Do not silently discard numeric/material/etc. constraints on graph queries."""
    if len(message) > 2000:
        raise HTTPException(
            422, "Assembly-aware feature questions must be at most 2,000 characters."
        )
    request, warnings, _ = deterministic_interpret(AISearchRequest(query=message))
    filters = request.filters.model_dump(exclude_none=True, exclude_defaults=True)
    extra = set(filters) - {"feature_filters"}
    negative = any(row.presence == "absent" for row in request.filters.feature_filters)
    feature_words = {
        word
        for feature in (*SUPPORTED_FEATURES, *UNSUPPORTED_FEATURES)
        for word in (feature, feature + ("es" if feature == "boss" else "s"))
    }
    residual = set(request.text.terms) - _QUESTION_WORDS - feature_words
    # The search interpreter can consume a quantity without producing a filter
    # (for example "6 mm holes"). This graph path must not broaden that query.
    numeric = bool(re.search(r"\d", message))
    if (
        extra
        or residual
        or numeric
        or (discovery and negative)
        or any("contradict" in row.lower() for row in warnings)
    ):
        raise HTTPException(
            422,
            "Assembly-aware feature queries currently support positive feature discovery and "
            "recorded totals. Additional filters or global absence searches are not supported.",
        )


@dataclass(frozen=True)
class Evidence:
    count: int | None
    complete: bool


def feature_evidence(extraction, revision_id, snapshot_id, features) -> dict[str, Evidence]:
    usable = bool(
        extraction
        and extraction.revision_id == revision_id
        and extraction.status in {"READY", "PARTIAL"}
        and (snapshot_id is None or extraction.snapshot_id == snapshot_id)
    )
    metadata = (extraction.metadata_json or {}) if usable else {}
    quality = metadata.get("quality", {}).get("features")
    section = metadata.get("extraction", {}).get("sections", {}).get("features", {})
    invalid = {"unavailable", "unsupported", "failed", "not_applicable"}
    usable = usable and quality not in invalid and section.get("status") not in invalid
    complete = quality == "complete" and section.get("status") in {None, "complete"}
    result = {}
    for feature in features:
        count = getattr(extraction, f"{feature}_count", None) if usable else None
        if feature not in SUPPORTED_FEATURES:
            count = None
        # Partial zero is not evidence of absence.
        result[feature] = Evidence(count, count is not None and complete)
    return result


@dataclass
class FeatureReport:
    item: Item
    revision: Revision
    totals: dict[str, int]
    complete: dict[str, bool]
    components: list[dict] = field(default_factory=list)
    assembly_features: list[dict] = field(default_factory=list)
    warnings: set[str] = field(default_factory=set)

    @staticmethod
    def counts_text(row):
        values = []
        for feature, count in row["counts"].items():
            complete = row["complete"][feature]
            if count is None or (count == 0 and not complete):
                value = "unknown"
            else:
                value = f"{count}" if complete else f"at least {count}"
            values.append(f"{feature}={value}")
        return ", ".join(values)

    def text(self) -> str:
        lines = [f"{self.item.part_number}, revision {self.revision.revision_code}:"]
        scope = "active component occurrences" if self.item.part_type == "ASSEMBLY" else "this part"
        for feature, count in self.totals.items():
            if self.complete[feature]:
                lines.append(f"{count} recorded {feature}(s) across {scope} (complete coverage).")
            elif count:
                lines.append(
                    f"At least {count} recorded {feature}(s) across {scope}; incomplete coverage."
                )
            else:
                lines.append(f"{feature.title()} count is unknown; no complete total is available.")
        for row in self.components[:25]:
            counts = self.counts_text(row)
            lines.append(
                f"- {' / '.join(row['path']) or row['item_number']}: "
                f"{row['item_number']}, revision "
                f"{row['revision_code']}, quantity {row['quantity']}; {counts}."
            )
        if len(self.components) > 25:
            lines.append(
                "Component evidence display is limited to 25 paths; totals use all visited paths."
            )
        if self.assembly_features:
            lines.append(
                "This is assembly-level evidence, recorded separately and "
                "not added to component totals."
            )
            for row in self.assembly_features[:25]:
                counts = self.counts_text(row)
                lines.append(
                    f"Assembly-level {row['item_number']}, "
                    f"revision {row['revision_code']}: {counts}."
                )
        lines.extend(sorted(self.warnings))
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "revision_id": str(self.revision.id),
            "totals": self.totals,
            "complete": self.complete,
            "components": self.components,
            "assembly_features": self.assembly_features,
            "warnings": sorted(self.warnings),
        }


class AssemblyFeatureSearch:
    def __init__(self, db: Session, principal: Principal):
        self.db, self.principal = db, principal
        self._nodes = {}
        self._snapshots = {}
        self._boms = {}
        self._evidence = {}
        self._extractions = {}
        self._parents_cache = {}
        self._parent_truncated = False

    def node(self, revision_id):
        if revision_id not in self._nodes:
            revision = self.db.get(Revision, revision_id)
            item = self.db.get(Item, revision.part_id) if revision else None
            pair = None
            if item is not None and item.status == "ACTIVE":
                try:
                    readable_item_revision(self.db, item.id, revision.id, self.principal)
                    pair = (item, revision)
                except HTTPException as error:
                    if error.status_code not in {403, 404}:
                        raise
            self._nodes[revision_id] = pair
        return self._nodes[revision_id]

    def snapshot(self, revision_id):
        if revision_id not in self._snapshots:
            self._snapshots[revision_id] = latest_content_snapshot_id(self.db, revision_id)
        return self._snapshots[revision_id]

    def bom(self, revision_id):
        if revision_id not in self._boms:
            snapshot_id = self.snapshot(revision_id)
            bom = (
                self.db.scalar(
                    select(BomViewRevision).where(
                        BomViewRevision.revision_id == revision_id,
                        BomViewRevision.snapshot_id == snapshot_id,
                    )
                )
                if snapshot_id
                else None
            )
            # Before the publication fix, regeneration inserted a derived-only
            # content version without a BOM. Prove that the source is unchanged
            # before inheriting its committed BOM; normal CAD commits never use
            # this compatibility path.
            baseline = self.db.get(RevisionContentSnapshot, snapshot_id) if snapshot_id else None
            remaining = MAX_DEPTH
            while (
                remaining
                and bom is None
                and baseline is not None
                and (baseline.comment == "Worker-derived Artifact publication")
            ):
                remaining -= 1
                previous = self.db.scalar(
                    select(RevisionContentSnapshot)
                    .where(
                        RevisionContentSnapshot.revision_id == revision_id,
                        RevisionContentSnapshot.sequence < baseline.sequence,
                    )
                    .order_by(RevisionContentSnapshot.sequence.desc())
                    .limit(1)
                )
                if previous is None:
                    break
                source_ids = self._source_relations(baseline.id)
                if not source_ids or source_ids != self._source_relations(previous.id):
                    break
                baseline = previous
                bom = self.db.scalar(
                    select(BomViewRevision).where(
                        BomViewRevision.revision_id == revision_id,
                        BomViewRevision.snapshot_id == baseline.id,
                    )
                )
            lines = (
                list(
                    self.db.scalars(
                        select(BomLine)
                        .where(
                            BomLine.bom_view_revision_id == bom.id,
                            BomLine.parent_revision_id == revision_id,
                            BomLine.is_suppressed.is_(False),
                        )
                        .order_by(BomLine.sequence, BomLine.id)
                        .limit(MAX_OCCURRENCES + 1)
                    )
                )
                if bom
                else []
            )
            self._boms[revision_id] = (bom, lines)
        return self._boms[revision_id]

    def _source_relations(self, snapshot_id):
        return set(
            self.db.scalars(
                select(RevisionContentSnapshotArtifact.relation_id)
                .join(
                    RevisionArtifactRelation,
                    RevisionArtifactRelation.id == RevisionContentSnapshotArtifact.relation_id,
                )
                .join(Artifact, Artifact.id == RevisionArtifactRelation.artifact_id)
                .where(
                    RevisionContentSnapshotArtifact.snapshot_id == snapshot_id,
                    Artifact.classification != "DERIVED",
                )
            )
        )

    def evidence(self, revision, features):
        key = (revision.id, tuple(features))
        if key not in self._evidence:
            extraction = self.extraction(revision)
            self._evidence[key] = feature_evidence(
                extraction,
                revision.id,
                self.snapshot(revision.id),
                features,
            )
        return self._evidence[key]

    def extraction(self, revision):
        if revision.id not in self._extractions:
            self._extractions[revision.id] = (
                self.db.get(MetadataExtraction, revision.current_metadata_extraction_id)
                if revision.current_metadata_extraction_id
                else None
            )
        return self._extractions[revision.id]

    def count(self, revision_id: uuid.UUID, features: list[str]) -> FeatureReport:
        root = self.node(revision_id)
        if root is None:
            raise HTTPException(404, "Item revision not found")
        report = FeatureReport(
            *root, {f: 0 for f in features}, {f: f in SUPPORTED_FEATURES for f in features}
        )
        visited = 0

        def incomplete(reason):
            report.warnings.add(reason)
            report.complete = {f: False for f in features}

        def visit(current_id, quantity, path, ancestors):
            nonlocal visited
            if visited >= MAX_OCCURRENCES:
                incomplete(
                    f"Traversal limited to {MAX_OCCURRENCES} occurrences; totals are incomplete."
                )
                return
            visited += 1
            if current_id in ancestors:
                incomplete("A BOM cycle was omitted; totals are incomplete.")
                return
            if len(ancestors) > MAX_DEPTH:
                incomplete(f"Traversal limited to depth {MAX_DEPTH}; totals are incomplete.")
                return
            pair = self.node(current_id)
            if pair is None:
                incomplete(
                    "Unavailable or inaccessible component evidence was omitted; "
                    "totals are incomplete."
                )
                return
            item, revision = pair
            evidence = self.evidence(revision, features)
            row = {
                "item_id": str(item.id),
                "revision_id": str(revision.id),
                "item_number": item.part_number,
                "revision_code": revision.revision_code,
                "path": path,
                "quantity": quantity,
                "counts": {f: e.count for f, e in evidence.items()},
                "complete": {f: e.complete for f, e in evidence.items()},
            }
            if item.part_type == "ASSEMBLY":
                report.assembly_features.append(row)
                bom, lines = self.bom(revision.id)
                if bom is None:
                    incomplete(
                        "Committed BOM coverage is unavailable; component totals are incomplete."
                    )
                    return
                extraction = self.extraction(revision)
                if (
                    extraction
                    and extraction.revision_id == revision.id
                    and extraction.snapshot_id == self.snapshot(revision.id)
                ):
                    metadata = extraction.metadata_json or {}
                    structure_quality = metadata.get("quality", {}).get("assembly")
                    section_quality = (
                        metadata.get("extraction", {})
                        .get("sections", {})
                        .get("assembly", {})
                        .get("status")
                    )
                    incomplete_states = {"partial", "unavailable", "unsupported", "failed"}
                    if (
                        structure_quality in incomplete_states
                        or section_quality in incomplete_states
                    ):
                        incomplete(
                            "Assembly structure coverage is incomplete; totals are lower bounds."
                        )
                for line in lines:
                    if visited >= MAX_OCCURRENCES:
                        incomplete(
                            f"Traversal limited to {MAX_OCCURRENCES} occurrences; "
                            "totals are incomplete."
                        )
                        break
                    visit(
                        line.child_revision_id,
                        quantity * line.quantity,
                        [*path, line.occurrence_key],
                        ancestors | {current_id},
                    )
            else:
                report.components.append(row)
                for feature, value in evidence.items():
                    report.totals[feature] += (value.count or 0) * quantity
                    report.complete[feature] &= value.complete

        visit(revision_id, 1, [], frozenset())
        return report

    def parents(self, child_revision_id):
        if child_revision_id not in self._parents_cache:
            rows = self.db.execute(
                select(BomLine, BomViewRevision)
                .join(
                    BomViewRevision,
                    BomViewRevision.id == BomLine.bom_view_revision_id,
                )
                .where(
                    BomLine.child_revision_id == child_revision_id, BomLine.is_suppressed.is_(False)
                )
                .order_by(BomLine.id)
                .limit(MAX_OCCURRENCES + 1)
            ).all()
            if len(rows) > MAX_OCCURRENCES:
                self._parent_truncated = True
                rows = rows[:MAX_OCCURRENCES]
            parents = []
            for line, bom in rows:
                pair = self.node(line.parent_revision_id)
                if pair is None or pair[0].part_type != "ASSEMBLY":
                    continue
                current_bom, _ = self.bom(pair[1].id)
                if current_bom and current_bom.id == bom.id and bom.revision_id == pair[1].id:
                    parents.append(line)
            self._parents_cache[child_revision_id] = parents
        return self._parents_cache[child_revision_id]

    def discover(self, features: list[str], *, limit=20, match_any=False):
        supported = [f for f in features if f in SUPPORTED_FEATURES]
        warnings = set()
        if len(supported) != len(features):
            warnings.add("Unsupported features have unknown coverage and cannot establish a match.")
        if not supported:
            return [], {}, warnings
        seeds = list(
            self.db.scalars(
                select(Revision.id)
                .join(
                    Item,
                    Item.id == Revision.part_id,
                )
                .join(
                    MetadataExtraction,
                    MetadataExtraction.id == Revision.current_metadata_extraction_id,
                )
                .where(
                    Item.status == "ACTIVE",
                    Item.part_type == "PART",
                    or_(*(getattr(MetadataExtraction, f"{f}_count") > 0 for f in supported)),
                )
                .order_by(Item.part_number, Revision.revision_code, Revision.id)
                .limit(MAX_SEEDS + 1)
            )
        )
        if len(seeds) > MAX_SEEDS:
            warnings.add(
                f"Discovery limited to {MAX_SEEDS} candidate part revisions; "
                "results are incomplete."
            )
        candidates = set()
        paths = {}
        visits = 0

        def upward(current_id, path, ancestors):
            nonlocal visits
            if visits >= MAX_OCCURRENCES or len(ancestors) > MAX_DEPTH:
                warnings.add(
                    "Parent discovery reached its traversal limit; results are incomplete."
                )
                return
            visits += 1
            for line in self.parents(current_id):
                if line.parent_revision_id in ancestors:
                    warnings.add("A cycle in parent discovery was omitted.")
                    continue
                next_path = [str(line.id), *path]
                candidates.add(line.parent_revision_id)
                paths.setdefault(line.parent_revision_id, []).append(next_path)
                if visits >= MAX_OCCURRENCES:
                    warnings.add(
                        "Parent discovery reached its traversal limit; results are incomplete."
                    )
                    break
                upward(line.parent_revision_id, next_path, ancestors | {line.parent_revision_id})

        for seed in seeds[:MAX_SEEDS]:
            pair = self.node(seed)
            if pair is None:
                continue
            evidence = self.evidence(pair[1], supported)
            if not any((e.count or 0) > 0 for e in evidence.values()):
                continue
            candidates.add(seed)
            upward(seed, [], frozenset({seed}))
        reports = []
        # Discovery is bounded independently from each downward counting operation.
        ordered = sorted(
            candidates,
            key=lambda key: (
                self.node(key)[0].part_number,
                self.node(key)[1].revision_code,
                str(key),
            ),
        )
        for candidate in ordered[:MAX_OCCURRENCES]:
            report = self.count(candidate, features)
            matches = [(report.totals[f] > 0) for f in features]
            if any(matches) if match_any else all(matches):
                reports.append(report)
                if len(reports) > limit:
                    break
        if len(ordered) > MAX_OCCURRENCES or len(reports) > limit:
            warnings.add(
                "Matching revisions were truncated; narrow the search for additional results."
            )
        if self._parent_truncated:
            warnings.add("Parent discovery candidates were truncated; results are incomplete.")
        return reports[:limit], paths, warnings
