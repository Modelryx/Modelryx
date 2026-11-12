import hashlib
import json
from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelryx.core.config import Settings
from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry
from modelryx.models import (
    DuplicateCandidate,
    DuplicateCheck,
    Item,
    Revision,
    StagingFile,
    StagingSession,
    ValidationAIAnalysis,
    ValidationFinding,
    ValidationFindingAction,
    ValidationRun,
)
from modelryx.services.ai import AiGateway
from modelryx.validation.models import AI_OUTPUT_SCHEMA, ALLOWED_ACTIONS, AIOutput, FindingInput

POLICY_VERSION = "phase6-mvp-v1"
PROMPT_VERSION = "phase6-validation-v1"
SOURCE = {"validator": "phase6_staging_validator", "version": POLICY_VERSION}
SUPPORTED_CAD_EXTENSIONS = frozenset(
    {
        ".fcstd",
        ".dwg",
        ".dxf",
        ".ipt",
        ".iam",
        ".idw",
        ".ipn",
        ".ide",
        ".step",
        ".stp",
        ".iges",
        ".igs",
    }
)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def resolve_status(findings: list[FindingInput]) -> str:
    if any(f.blocking for f in findings):
        return "BLOCKED"
    if any(f.severity == "WARNING" for f in findings):
        return "READY_WITH_WARNINGS"
    return "READY"


def summarize(findings: list[FindingInput]) -> dict[str, int]:
    counts = Counter(f.severity.lower() for f in findings)
    return {
        "critical": counts["critical"],
        "error": counts["error"],
        "warning": counts["warning"],
        "info": counts["info"],
        "blocking": sum(f.blocking for f in findings),
        "total": len(findings),
    }


def validator_state(
    findings: list[FindingInput], categories: set[str], *, evaluated: bool = True
) -> str:
    """Return the strongest deterministic state for one validator domain."""
    if not evaluated:
        return "NOT_EVALUATED"
    relevant = [finding for finding in findings if finding.category in categories]
    if any(finding.blocking or finding.severity in {"ERROR", "CRITICAL"} for finding in relevant):
        return "FAIL"
    if any(finding.severity == "WARNING" for finding in relevant):
        return "WARNING"
    return "PASS"


def can_acknowledge(finding: ValidationFinding | FindingInput) -> bool:
    actions = finding.actions_json if isinstance(finding, ValidationFinding) else finding.actions
    return not finding.blocking and finding.severity == "WARNING" and "ignore_warning" in actions


def geometry_findings(metadata: dict[str, Any]) -> list[FindingInput]:
    geometry = metadata.get("geometry") or {}
    shape = geometry.get("shape") or {}
    topology = geometry.get("topology") or {}
    bbox = geometry.get("bounding_box") or {}
    findings: list[FindingInput] = []
    volume = shape.get("volume_mm3")
    area = shape.get("surface_area_mm2")
    solid_count = topology.get("solid_count")
    dimensions = [bbox.get(axis) for axis in ("x_mm", "y_mm", "z_mm")]
    warnings = [str(value) for value in (metadata.get("quality") or {}).get("warnings", [])]
    body_geometry_unavailable = any(
        "no surface or solid bodies" in warning.casefold() for warning in warnings
    )

    if has_unverified_array_geometry(metadata):
        return [
            FindingInput(
                code="ARRAY_GEOMETRY_UNVERIFIED",
                category="geometry",
                severity="WARNING",
                blocking=False,
                title="Array geometry could not be verified",
                message=ARRAY_GEOMETRY_WARNING,
                source=SOURCE,
                details={"geometry_validation": "incomplete"},
                actions=["open_in_cad", "revalidate"],
            )
        ]

    if body_geometry_unavailable:
        return [
            FindingInput(
                code="BODY_GEOMETRY_UNAVAILABLE",
                category="geometry",
                severity="WARNING",
                blocking=False,
                title="Body properties are unavailable",
                message=(
                    "This part contains points and curves but no surface or solid bodies. "
                    "Volume, surface area, and mass properties cannot be generated; "
                    "the 3D preview shows its reference points and curves."
                ),
                source=SOURCE,
                details={"geometry_kind": "points_and_curves"},
                actions=["open_in_cad", "ignore_warning", "revalidate"],
            )
        ]

    if volume is not None and float(volume) <= 0:
        findings.append(
            FindingInput(
                code="GEOMETRY_ZERO_VOLUME",
                category="geometry",
                severity="ERROR",
                blocking=True,
                title="Geometry has zero volume",
                message="Normalized geometry reports no positive solid volume.",
                source=SOURCE,
                details={"volume_mm3": volume},
                actions=["open_in_cad", "replace_file", "revalidate"],
            )
        )
    if any(value is None or float(value) <= 0 for value in dimensions):
        findings.append(
            FindingInput(
                code="GEOMETRY_BBOX_INVALID",
                category="geometry",
                severity="ERROR",
                blocking=True,
                title="Geometry bounding box is invalid",
                message="All three normalized bounding-box dimensions must be positive.",
                source=SOURCE,
                details={"bounding_box": bbox},
                actions=["open_in_cad", "replace_file", "revalidate"],
            )
        )
    if area is not None and float(area) <= 0:
        findings.append(
            FindingInput(
                code="GEOMETRY_INVALID",
                category="geometry",
                severity="ERROR",
                blocking=True,
                title="Geometry surface area is invalid",
                message="Normalized geometry reports no positive surface area.",
                source=SOURCE,
                details={"surface_area_mm2": area},
                actions=["open_in_cad", "replace_file", "revalidate"],
            )
        )
    if solid_count == 0 and volume is not None and float(volume) > 0:
        findings.append(
            FindingInput(
                code="GEOMETRY_INVALID",
                category="geometry",
                severity="ERROR",
                blocking=True,
                title="Geometry topology is inconsistent",
                message="Positive volume was reported while the solid count is zero.",
                source=SOURCE,
                details={"solid_count": solid_count, "volume_mm3": volume},
                actions=["open_in_cad", "replace_file", "revalidate"],
            )
        )
    return findings


def similarity_evaluable(findings: list[FindingInput]) -> bool:
    """Similarity needs usable normalized metadata; exact hashes do not."""
    return not any(
        finding.blocking and finding.category in {"metadata", "geometry"} for finding in findings
    )


def metadata_quality_findings(metadata: dict[str, Any]) -> list[FindingInput]:
    quality = metadata.get("quality") or {}
    identity = metadata.get("identity") or {}
    physical = metadata.get("physical") or metadata.get("properties") or {}
    findings: list[FindingInput] = []

    if physical.get("mass_kg") is None:
        findings.append(
            FindingInput(
                code="MASS_UNAVAILABLE",
                category="metadata",
                severity="INFO",
                blocking=False,
                title="Mass is unavailable",
                message="Normalized engineering metadata does not include a mass value.",
                source=SOURCE,
                evidence={"physical_quality": quality.get("physical")},
                actions=["set_material", "revalidate"],
            )
        )

    tracked_domains = ["geometry", "physical"]
    if identity.get("kind") == "assembly":
        tracked_domains.append("assembly")
    incomplete = {
        domain: quality.get(domain)
        for domain in tracked_domains
        if quality.get(domain) in {"partial", "unavailable"}
    }
    if incomplete:
        findings.append(
            FindingInput(
                code="METADATA_QUALITY_INCOMPLETE",
                category="metadata",
                severity="WARNING",
                blocking=False,
                title="Engineering metadata is incomplete",
                message="One or more worker-declared metadata domains are partial or unavailable.",
                source=SOURCE,
                details={"domains": incomplete},
                actions=["edit_metadata", "ignore_warning", "revalidate"],
            )
        )

    for index, warning in enumerate(quality.get("warnings") or []):
        text = str(warning).strip()
        if not text:
            continue
        findings.append(
            FindingInput(
                code="WORKER_REPORTED_WARNING",
                category="worker",
                severity="WARNING",
                blocking=False,
                title="Worker reported a metadata warning",
                message=text[:10000],
                source=SOURCE,
                location={"warning_index": index},
                evidence={"worker_message": text[:10000]},
                actions=["open_in_cad", "ignore_warning", "revalidate"],
            )
        )
    return findings


class ValidationService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def _file_findings(self, staging: StagingSession) -> list[FindingInput]:
        files = list(
            self.db.scalars(select(StagingFile).where(StagingFile.staging_id == staging.id))
        )
        findings: list[FindingInput] = []
        proposal = staging.proposal_json or {}
        reused_ids = {
            str(decision.get("staged_file_id"))
            for decision in proposal.get("identity_decisions", [])
            if decision.get("decision") == "USE_EXISTING"
        }
        for item in files:
            location = {"staging_file_id": str(item.id), "file_name": item.filename}
            if item.byte_size == 0:
                findings.append(
                    FindingInput(
                        code="FILE_EMPTY",
                        category="file",
                        severity="ERROR",
                        blocking=True,
                        title="Empty file",
                        message=f"{item.filename} contains no data.",
                        source=SOURCE,
                        location=location,
                        evidence={"byte_size": 0},
                        actions=["replace_file", "remove_file"],
                    )
                )
            elif item.state != "VERIFIED" and str(item.id) not in reused_ids:
                findings.append(
                    FindingInput(
                        code="WORKER_OUTPUT_MISSING",
                        category="worker",
                        severity="ERROR",
                        blocking=True,
                        title="File content not verified",
                        message=f"{item.filename} has not completed upload verification.",
                        source=SOURCE,
                        location=location,
                        evidence={"file_state": item.state},
                        actions=["replace_file", "revalidate"],
                    )
                )
            extension = (
                "." + item.filename.rsplit(".", 1)[-1].lower() if "." in item.filename else ""
            )
            if extension not in SUPPORTED_CAD_EXTENSIONS:
                findings.append(
                    FindingInput(
                        code="FILE_UNSUPPORTED",
                        category="file",
                        severity="WARNING",
                        blocking=False,
                        title="File type is not recognized as CAD",
                        message=f"{item.filename} has an unrecognized CAD extension.",
                        source=SOURCE,
                        location=location,
                        evidence={"extension": extension or None, "mime_type": item.mime_type},
                        actions=["replace_file", "remove_file", "ignore_warning", "revalidate"],
                    )
                )
        return findings

    def _metadata_findings(self, staging: StagingSession) -> list[FindingInput]:
        metadata = staging.metadata_json
        if metadata is None:
            return [
                FindingInput(
                    code="METADATA_MISSING",
                    category="metadata",
                    severity="WARNING",
                    blocking=False,
                    title="Engineering metadata pending",
                    message=(
                        "Normalized PartMetadata is not available yet and will be extracted "
                        "from the committed CAD file."
                    ),
                    source=SOURCE,
                    actions=["ignore_warning", "revalidate"],
                )
            ]
        findings: list[FindingInput] = []
        findings.extend(geometry_findings(metadata))
        physical = metadata.get("physical") or metadata.get("properties") or {}
        material = physical.get("material") or metadata.get("material")
        if not material:
            findings.append(
                FindingInput(
                    code="MATERIAL_MISSING",
                    category="metadata",
                    severity="WARNING",
                    blocking=False,
                    title="Material property is missing",
                    message="No material is assigned in normalized engineering metadata.",
                    source=SOURCE,
                    evidence={"metadata_present": True},
                    actions=["set_material", "ignore_warning", "revalidate"],
                )
            )
        findings.extend(metadata_quality_findings(metadata))
        return findings

    def _duplicate_findings(
        self, staging: StagingSession, *, allow_similarity: bool = True
    ) -> list[FindingInput]:
        check = self.db.scalar(
            select(DuplicateCheck)
            .where(DuplicateCheck.source_staging_id == staging.id)
            .order_by(DuplicateCheck.created_at.desc(), DuplicateCheck.id.desc())
        )
        if check is None or check.status != "COMPLETED":
            return []
        rows = self.db.execute(
            select(DuplicateCandidate, Revision, Item)
            .join(Revision, Revision.id == DuplicateCandidate.candidate_revision_id)
            .join(Item, Item.id == Revision.part_id)
            .where(DuplicateCandidate.duplicate_check_id == check.id)
        ).all()
        findings: list[FindingInput] = []
        decisions = {
            str(row.get("staged_file_id")): row.get("decision")
            for row in (staging.proposal_json or {}).get("identity_decisions", [])
            if isinstance(row, dict)
        }
        explicit_create = "CREATE_NEW" in decisions.values()
        for candidate, revision, item in rows:
            score = float(candidate.score_breakdown_json.get("combined") or 0)
            # A similarity score, even 1.0, is not proof of byte identity. Only the
            # duplicate subsystem's exact-file classification is authoritative here.
            exact = candidate.classification == "exact_file_duplicate"
            if not exact and not allow_similarity:
                continue
            if not exact and score < 0.75:
                continue
            code = (
                "DUPLICATE_EXACT"
                if exact
                else ("DUPLICATE_HIGH_SIMILARITY" if score >= 0.90 else "DUPLICATE_POSSIBLE")
            )
            severity = (
                "WARNING"
                if exact and explicit_create
                else "ERROR"
                if exact
                else "WARNING"
                if score >= 0.90
                else "INFO"
            )
            findings.append(
                FindingInput(
                    code=code,
                    category="duplicate",
                    severity=severity,
                    blocking=exact and not explicit_create,
                    title="Exact duplicate detected"
                    if exact
                    else "Similar existing revision detected",
                    message=(
                        f"The staged package is {score:.0%} similar to "
                        f"{item.part_number} Rev {revision.revision_code}."
                    ),
                    source={"validator": "duplicate_detection", "version": check.algorithm_version},
                    details={
                        "candidate_item": item.part_number,
                        "candidate_revision": revision.revision_code,
                        "candidate_revision_id": str(revision.id),
                        "similarity": score,
                    },
                    evidence={
                        "classification": candidate.classification,
                        "reasons": candidate.reasons_json,
                    },
                    actions=["use_existing_item", "create_new_item", "review_duplicate"]
                    if exact and not explicit_create
                    else ["use_existing_item", "review_duplicate"]
                    if exact
                    else ["review_duplicate", "use_existing_item", "create_new_revision"],
                )
            )
        return findings

    def run(self, staging: StagingSession, actor_id: UUID) -> ValidationRun:
        findings = self._file_findings(staging) + self._metadata_findings(staging)
        allow_similarity = similarity_evaluable(findings)
        duplicate_findings = self._duplicate_findings(staging, allow_similarity=allow_similarity)
        findings += duplicate_findings
        status = resolve_status(findings)
        summary = summarize(findings)
        payload = {
            "status": status,
            "summary": summary,
            "findings": [f.model_dump(mode="json") for f in findings],
            "policy_version": POLICY_VERSION,
        }
        number = (
            int(
                self.db.scalar(
                    select(func.count())
                    .select_from(ValidationRun)
                    .where(ValidationRun.staging_id == staging.id)
                )
                or 0
            )
            + 1
        )
        run = ValidationRun(
            staging_id=staging.id,
            snapshot_version=staging.snapshot_version,
            run_number=number,
            status=status,
            report_hash=_canonical_hash(payload),
            policy_version=POLICY_VERSION,
            summary_json=summary,
            validator_states_json={
                "file": validator_state(findings, {"file", "worker"}),
                "metadata": validator_state(
                    findings, {"metadata"}, evaluated=staging.metadata_json is not None
                ),
                "geometry": validator_state(
                    findings, {"geometry"}, evaluated=staging.metadata_json is not None
                ),
                "duplicate": validator_state(
                    findings,
                    {"duplicate"},
                    evaluated=allow_similarity
                    or any(item.code == "DUPLICATE_EXACT" for item in duplicate_findings),
                ),
            },
            created_by=actor_id,
        )
        self.db.add(run)
        self.db.flush()
        for finding in findings:
            self.db.add(
                ValidationFinding(
                    validation_run_id=run.id,
                    code=finding.code,
                    category=finding.category,
                    severity=finding.severity,
                    blocking=finding.blocking,
                    title=finding.title,
                    message=finding.message,
                    source_json=finding.source,
                    location_json=finding.location,
                    details_json=finding.details,
                    evidence_json=finding.evidence,
                    actions_json=finding.actions,
                )
            )
        self.db.flush()
        return run

    def latest(self, staging_id: UUID) -> ValidationRun | None:
        return self.db.scalar(
            select(ValidationRun)
            .where(ValidationRun.staging_id == staging_id)
            .order_by(ValidationRun.run_number.desc())
        )

    def history(self, staging_id: UUID, limit: int = 20) -> list[ValidationRun]:
        return list(
            self.db.scalars(
                select(ValidationRun)
                .where(ValidationRun.staging_id == staging_id)
                .order_by(ValidationRun.run_number.desc())
                .limit(limit)
            )
        )

    def findings(self, run_id: UUID) -> list[ValidationFinding]:
        rank = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
        values = list(
            self.db.scalars(
                select(ValidationFinding).where(ValidationFinding.validation_run_id == run_id)
            )
        )
        return sorted(values, key=lambda f: (not f.blocking, rank[f.severity], f.code, str(f.id)))

    def acknowledgement(self, finding_id: UUID) -> ValidationFindingAction | None:
        return self.db.scalar(
            select(ValidationFindingAction).where(ValidationFindingAction.finding_id == finding_id)
        )

    def analysis(self, run: ValidationRun) -> ValidationAIAnalysis:
        cache_key = _canonical_hash(
            {
                "report_hash": run.report_hash,
                "model": self.settings.ai_chat_model or "disabled",
                "prompt": PROMPT_VERSION,
            }
        )
        cached = self.db.scalar(
            select(ValidationAIAnalysis).where(ValidationAIAnalysis.cache_key == cache_key)
        )
        if cached:
            return cached
        findings = self.findings(run.id)
        context = {
            "validation_status": run.status,
            "summary": run.summary_json,
            "findings": [
                {
                    "id": str(f.id),
                    "code": f.code,
                    "severity": f.severity,
                    "blocking": f.blocking,
                    "title": f.title,
                    "message": f.message,
                    "location": f.location_json,
                    "details": f.details_json,
                    "evidence": f.evidence_json,
                    "allowed_actions": f.actions_json,
                }
                for f in findings
            ],
        }
        output = self._fallback(run, findings)
        mode, warnings = (
            "DETERMINISTIC",
            ["AI explanation unavailable; deterministic explanation returned."],
        )
        gateway = AiGateway(self.settings)
        if gateway.chat_enabled:
            try:
                candidate = AIOutput.model_validate(
                    gateway.structured_chat(
                        system=(
                            "You are the Modelryx Validation Assistant. "
                            "Findings are authoritative. "
                            "Never alter severity, blocking state, identifiers, measurements, "
                            "filenames, revisions, or similarity. Explain only supplied facts. "
                            "Recommend only each finding's allowed_actions."
                        ),
                        payload=context,
                        schema=AI_OUTPUT_SCHEMA,
                        schema_name="modelryx_validation_analysis",
                    )
                )
                self._validate_grounding(candidate, findings)
                output, mode, warnings = candidate, "AI", []
            except (HTTPException, ValidationError, ValueError):
                pass
        analysis = ValidationAIAnalysis(
            validation_run_id=run.id,
            mode=mode,
            model=self.settings.ai_chat_model if mode == "AI" else None,
            prompt_version=PROMPT_VERSION,
            cache_key=cache_key,
            output_json=output.model_dump(mode="json"),
            warnings_json=warnings,
        )
        self.db.add(analysis)
        self.db.flush()
        return analysis

    @staticmethod
    def _validate_grounding(output: AIOutput, findings: list[ValidationFinding]) -> None:
        allowed = {f.id: set(f.actions_json) for f in findings}
        if {issue.finding_id for issue in output.issues} != set(allowed):
            raise ValueError("AI output must explain every and only supplied finding")
        for issue in output.issues:
            actions = {action.action for action in issue.recommended_actions}
            if not actions <= allowed[issue.finding_id] or not actions <= ALLOWED_ACTIONS:
                raise ValueError("AI output invented a remediation action")

    @staticmethod
    def _fallback(run: ValidationRun, findings: list[ValidationFinding]) -> AIOutput:
        if run.status == "READY":
            headline, text = "Validation ready", "Deterministic validation found no active issues."
        else:
            headline = (
                "Validation blocked"
                if run.status == "BLOCKED"
                else "Validation completed with warnings"
            )
            text = (
                f"Deterministic validation found {run.summary_json['total']} issue(s), "
                f"including {run.summary_json['blocking']} blocking issue(s)."
            )
        return AIOutput(
            headline=headline,
            summary=text,
            issues=[
                {
                    "finding_id": f.id,
                    "explanation": f.message,
                    "impact": "This finding blocks promotion."
                    if f.blocking
                    else "This finding does not currently block promotion.",
                    "recommended_actions": [
                        {"action": action, "label": action.replace("_", " ").capitalize()}
                        for action in f.actions_json
                    ],
                }
                for f in findings
            ],
        )
