import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.api.assemblies import read_bom, where_used
from modelryx.api.items import readable_item_revision
from modelryx.comparison.explanations import explain_comparison, explanation_response
from modelryx.comparison.service import comparison_response, create_comparison
from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.engineering_search.interpreter import AISearchInterpreter
from modelryx.engineering_search.models import (
    AISearchRequest,
    SearchContext,
    SearchRequest,
    SimilaritySearch,
)
from modelryx.engineering_search.service import execute_search
from modelryx.models import (
    AuditLog,
    Item,
    MetadataExtraction,
    ProcessingJob,
    Revision,
)
from modelryx.services.ai import AiGateway
from modelryx.services.artifact_content import (
    artifact_files_for_snapshot,
    latest_content_snapshot_id,
)

from .feature_search import (
    SUPPORTED_FEATURES as _SUPPORTED_FEATURES,
)
from .feature_search import (
    AssemblyFeatureSearch,
    feature_evidence,
    requested_features,
    validate_feature_question,
)
from .models import (
    AssistantAction,
    AssistantComparisonAction,
    AssistantContext,
    AssistantReference,
    AssistantViewerAction,
)

AssistantIntent = Literal[
    "ITEM_SUMMARY",
    "ITEM_METADATA",
    "REVISION_HISTORY",
    "COMPARE_REVISIONS",
    "BOM_QUERY",
    "WHERE_USED",
    "SELECTED_COMPONENT",
    "ENGINEERING_SEARCH",
    "SIMILAR_PART_SEARCH",
    "VALIDATION_DIAGNOSIS",
    "VIEWER_HIGHLIGHT",
    "VIEWER_ISOLATE",
    "VIEWER_MODIFIED_COMPONENTS",
    "SELECTED_WHERE_USED",
]


@dataclass(frozen=True)
class ToolResult:
    answer: str
    references: list[AssistantReference]
    actions: list[AssistantAction | AssistantComparisonAction | AssistantViewerAction]
    summary: dict


def classify_intent(message: str, context: AssistantContext | None = None) -> AssistantIntent:
    normalized = re.sub(r"\s+", " ", message.casefold()).strip()
    has_selection = bool(
        context
        and (
            (context.assembly and context.assembly.selected_occurrence_id)
            or (context.selection and context.selection.object_ids)
        )
    )
    if re.search(
        r"\b(validation|validate|failed|failure|which file|caused the problem|what should i fix)\b",
        normalized,
    ):
        return "VALIDATION_DIAGNOSIS"
    if re.search(r"\b(modified|changed) components?\b", normalized) and re.search(
        r"\b(show|highlight|isolate)\b", normalized
    ):
        return "VIEWER_MODIFIED_COMPONENTS"
    if re.search(r"\bisolate\b", normalized):
        return "VIEWER_ISOLATE"
    viewer_show = bool(
        context
        and context.assembly
        and re.search(r"\bshow\b", normalized)
        and not re.search(r"\b(revision|history|bom|bill of materials)\b", normalized)
        and not requested_features(normalized)
    )
    if re.search(r"\bhighlight\b", normalized) or viewer_show:
        return "VIEWER_HIGHLIGHT"
    if has_selection and re.search(
        r"\b(where[- ]?used|where (?:else )?is (?:this|the selected).*(?:used))\b",
        normalized,
    ):
        return "SELECTED_WHERE_USED"
    if has_selection and re.search(r"\b(what is this|what is selected)\b", normalized):
        return "SELECTED_COMPONENT"
    if re.search(
        r"\b(selected component|selected part|what is selected|what is this component)\b",
        normalized,
    ):
        return "SELECTED_COMPONENT"
    if re.search(r"\b(similar|similarity|alternatives? like this)\b", normalized):
        return "SIMILAR_PART_SEARCH"
    if re.search(r"\b(where[- ]?used|where is .* used|assemblies use|used in which)\b", normalized):
        return "WHERE_USED"
    if re.search(r"\b(bom|bill of materials|components are in|assembly components)\b", normalized):
        return "BOM_QUERY"
    if re.search(r"\b(find|search|show me|look for|looking for)\b", normalized):
        return "ENGINEERING_SEARCH"
    if re.search(r"\b(changed|change|compare|different|difference|why was revision)\b", normalized):
        return "COMPARE_REVISIONS"
    if re.search(r"\b(revision history|revisions|revision list|revision timeline)\b", normalized):
        return "REVISION_HISTORY"
    if requested_features(normalized):
        return "ITEM_METADATA"
    if re.search(r"\b(material|alloy|steel|aluminum|aluminium)\b", normalized):
        return "ITEM_METADATA"
    if re.search(r"\b(summarize|summary|describe|what is this|tell me about)\b", normalized):
        return "ITEM_SUMMARY"
    raise HTTPException(
        status_code=422,
        detail=(
            "The assistant supports item summaries, material and CAD feature questions, "
            "revision history, "
            "revision comparisons, BOM questions, where-used, selected components, searches, "
            "and validation diagnosis."
        ),
    )


class AssistantToolGateway:
    """Read-only authorization boundary between assistant intent and domain data."""

    def __init__(self, db: Session, principal: Principal, settings: Settings | None = None):
        self.db = db
        self.principal = principal
        self.settings = settings

    def resolve_item_revision(self, context: AssistantContext) -> tuple[Item, Revision]:
        pointer = context.item
        if pointer is None:
            raise HTTPException(status_code=422, detail="Current item context is required")
        item = self.db.get(Item, pointer.item_id)
        if item is None or item.status != "ACTIVE":
            raise HTTPException(status_code=404, detail="Item not found")
        revision_id = pointer.revision_id or item.current_revision_id
        if revision_id is None:
            raise HTTPException(status_code=409, detail="Item has no current revision")
        revision = readable_item_revision(self.db, pointer.item_id, revision_id, self.principal)
        return item, revision

    def execute(
        self, intent: AssistantIntent, context: AssistantContext, message: str = ""
    ) -> tuple[str, ToolResult, float]:
        started = time.perf_counter()
        features = requested_features(message)
        feature_intent = intent in {"ITEM_METADATA", "ENGINEERING_SEARCH", "BOM_QUERY"}
        if features and feature_intent and context.item is None:
            validate_feature_question(message, discovery=True)
            result = self._discover_features(features, message)
            return "search_assembly_features", result, round(
                (time.perf_counter() - started) * 1000, 3
            )
        item, revision = self.resolve_item_revision(context)
        if features and feature_intent and item.part_type == "ASSEMBLY":
            validate_feature_question(message)
            result = self._assembly_features(item, revision, features)
            return "count_assembly_features", result, round(
                (time.perf_counter() - started) * 1000, 3
            )
        if intent == "ITEM_METADATA":
            tool_name = "get_item_metadata"
            features = requested_features(message)
            result = (
                self._item_features(item, revision, features)
                if features else self._item_material(item, revision)
            )
        elif intent == "REVISION_HISTORY":
            tool_name = "get_revision_history"
            result = self._revision_history(item)
        elif intent == "COMPARE_REVISIONS":
            tool_name = (
                "explain_revision_comparison"
                if re.search(r"\b(why|explain)\b", message, re.IGNORECASE)
                else "compare_revisions"
            )
            result = self._compare_revisions(item, revision, message)
        elif intent == "BOM_QUERY":
            tool_name = "get_bom"
            result = self._bom(item, revision)
        elif intent == "WHERE_USED":
            tool_name = "get_where_used"
            result = self._where_used(item, revision)
        elif intent == "SELECTED_COMPONENT":
            tool_name = "get_selected_component"
            result = self._selected_component(item, revision, context)
        elif intent == "ENGINEERING_SEARCH":
            tool_name = "search_parts"
            result = self._engineering_search(item, revision, message)
        elif intent == "SIMILAR_PART_SEARCH":
            tool_name = "find_similar_parts"
            result = self._similar_parts(item, revision)
        elif intent == "VALIDATION_DIAGNOSIS":
            tool_name = "get_validation_result"
            result = self._validation_diagnosis(item, revision, context)
        elif intent == "VIEWER_HIGHLIGHT":
            tool_name = "highlight_occurrences"
            result = self._viewer_matches(item, revision, message, isolate=False)
        elif intent == "VIEWER_ISOLATE":
            tool_name = "isolate_occurrences"
            result = self._viewer_matches(item, revision, message, isolate=True)
        elif intent == "VIEWER_MODIFIED_COMPONENTS":
            tool_name = "highlight_modified_components"
            result = self._viewer_modified_components(item, revision)
        elif intent == "SELECTED_WHERE_USED":
            tool_name = "get_selected_component_where_used"
            result = self._selected_where_used(item, revision, context)
        else:
            tool_name = "get_item"
            result = self._item_summary(item, revision)
        return tool_name, result, round((time.perf_counter() - started) * 1000, 3)

    def _reference(self, item: Item, revision: Revision, kind: Literal["ITEM", "METADATA"]):
        return AssistantReference(
            type=kind,
            item_id=item.id,
            revision_id=revision.id,
            item_number=item.part_number,
            revision_code=revision.revision_code,
            label=f"{item.part_number} — {item.name}, revision {revision.revision_code}",
        )

    def _action(self, item: Item, revision: Revision) -> AssistantAction:
        return AssistantAction(type="OPEN_REVISION", item_id=item.id, revision_id=revision.id)

    def _comparison_action(
        self, item: Item, before: Revision, after: Revision, comparison_id: uuid.UUID
    ) -> AssistantComparisonAction:
        return AssistantComparisonAction(
            type="OPEN_COMPARISON",
            item_id=item.id,
            from_revision_id=before.id,
            to_revision_id=after.id,
            comparison_id=comparison_id,
        )

    def _item_summary(self, item: Item, revision: Revision) -> ToolResult:
        category = f" It is classified as {item.category}." if item.category else ""
        description = f" {item.description.strip()}" if item.description else ""
        answer = (
            f"{item.part_number} is {item.name}, currently at revision "
            f"{revision.revision_code}.{category}{description} Its revision state is "
            f"{revision.state} and its native format is {revision.native_format}."
        )
        summary = {
            "item_id": str(item.id),
            "revision_id": str(revision.id),
            "item_number": item.part_number,
            "revision_code": revision.revision_code,
            "state": revision.state,
        }
        return ToolResult(
            answer,
            [self._reference(item, revision, "ITEM")],
            [self._action(item, revision)],
            summary,
        )

    def _search_result(self, response, *, heading: str) -> ToolResult:
        lines = [
            f"- {row.item_number} — {row.name}"
            + (f", revision {row.revision_code}" if row.revision_code else "")
            + f" (score {row.score:.3f})"
            for row in response.results
        ]
        answer = f"{heading} I found {response.total} result(s)." + (
            "\n" + "\n".join(lines) if lines else ""
        )
        references = [
            AssistantReference(
                type="SEARCH_RESULT",
                item_id=row.item_id,
                revision_id=row.revision_id,
                item_number=row.item_number,
                revision_code=row.revision_code,
                label=(
                    f"{row.item_number} — {row.name}"
                    + (f", revision {row.revision_code}" if row.revision_code else "")
                    + f" · {row.score:.3f}"
                ),
            )
            for row in response.results
        ]
        actions = [
            AssistantAction(
                type="OPEN_REVISION" if row.revision_id else "OPEN_ITEM",
                item_id=row.item_id,
                revision_id=row.revision_id,
            )
            for row in response.results
        ]
        return ToolResult(
            answer,
            references,
            actions,
            {
                "interpretation": response.interpretation.model_dump(mode="json"),
                "total": response.total,
                "returned": len(response.results),
                "warnings": response.warnings,
                "results": [
                    {
                        "item_id": str(row.item_id),
                        "revision_id": str(row.revision_id) if row.revision_id else None,
                        "score": row.score,
                        "match_reasons": [
                            reason.model_dump(mode="json") for reason in row.match_reasons
                        ],
                    }
                    for row in response.results
                ],
            },
        )

    def _engineering_search(self, item: Item, revision: Revision, message: str) -> ToolResult:
        if self.settings is None:
            raise HTTPException(status_code=503, detail="Assistant search is not configured")
        payload = AISearchRequest(
            query=message,
            context=SearchContext(
                current_item_id=item.id,
                current_revision_id=revision.id,
            ),
            limit=10,
        )
        request, warnings, confidence, parser = AISearchInterpreter(
            AiGateway(self.settings)
        ).interpret(payload)
        response = execute_search(
            self.db,
            self.principal,
            request,
            parser=parser,
            warnings=warnings,
            confidence=confidence,
        )
        return self._search_result(response, heading="Engineering search complete.")

    def _similar_parts(self, item: Item, revision: Revision) -> ToolResult:
        request = SearchRequest(
            entity_type="item",
            similarity=SimilaritySearch(source_item_revision_id=revision.id),
            limit=10,
        )
        response = execute_search(self.db, self.principal, request)
        return self._search_result(
            response,
            heading=f"Parts similar to {item.part_number}, revision {revision.revision_code}.",
        )

    @staticmethod
    def _job_errors(job: ProcessingJob) -> list[dict[str, str]]:
        diagnostics = job.diagnostics_json or {}
        raw_errors = diagnostics.get("errors", [])
        errors = []
        if isinstance(raw_errors, list):
            for raw in raw_errors[:25]:
                if isinstance(raw, dict):
                    message = str(raw.get("message") or raw.get("detail") or "").strip()
                    if message:
                        errors.append(
                            {
                                "stage": str(raw.get("stage") or job.stage),
                                "message": message[:1000],
                            }
                        )
                elif str(raw).strip():
                    errors.append({"stage": job.stage, "message": str(raw)[:1000]})
        if not errors and job.error_detail:
            errors.append({"stage": job.stage, "message": job.error_detail[:1000]})
        return errors

    @staticmethod
    def _recommended_fix(stage: str) -> str:
        normalized = stage.upper()
        if normalized == "VALIDATING":
            return (
                "Review the recorded validation error and repair the source geometry or output "
                "it identifies, then reprocess the revision."
            )
        if normalized == "CONVERTING":
            return (
                "Verify that the native CAD file opens cleanly and repair unsupported or invalid "
                "geometry before reprocessing."
            )
        if normalized == "PUBLISHING":
            return (
                "The generated output could not be published; retry processing and contact an "
                "administrator if the storage error repeats."
            )
        if normalized in {"QUEUED", "PROCESSING"}:
            return "Retry after confirming that the processing worker is available."
        return (
            "Use the recorded diagnostic to correct the source or processing configuration, "
            "then reprocess the revision."
        )

    def _validation_diagnosis(
        self,
        item: Item,
        revision: Revision,
        context: AssistantContext,
    ) -> ToolResult:
        pointer = context.validation
        if pointer is not None:
            job = self.db.get(ProcessingJob, pointer.job_id)
            if job is None or job.revision_id != revision.id:
                raise HTTPException(status_code=404, detail="Validation job not found")
        else:
            job = self.db.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.revision_id == revision.id)
                .order_by(ProcessingJob.available_at.desc(), ProcessingJob.id.desc())
            )
        if job is None:
            raise HTTPException(
                status_code=404,
                detail="No processing or validation result exists for this revision",
            )

        errors = self._job_errors(job)
        diagnosis_snapshot_id = job.snapshot_id or latest_content_snapshot_id(
            self.db, revision.id
        )
        source_files = (
            [
                artifact
                for artifact in artifact_files_for_snapshot(
                    self.db, revision.id, diagnosis_snapshot_id
                )
                if artifact.kind == "NATIVE"
            ]
            if diagnosis_snapshot_id
            else []
        )
        error_lines = [f"- {error['stage']}: {error['message']}" for error in errors]
        filenames = [artifact.original_filename for artifact in source_files]
        file_sentence = (
            f" Source file(s): {', '.join(filenames)}."
            if filenames
            else " No verified native source file is linked to this job."
        )
        if job.state == "FAILED":
            evidence = (
                "\nRecorded diagnostics:\n" + "\n".join(error_lines)
                if error_lines
                else " No detailed diagnostic was recorded."
            )
            diagnostic_stage = errors[0]["stage"] if errors else job.stage
            recommendation = self._recommended_fix(diagnostic_stage)
            answer = (
                f"The {job.type} job failed.{file_sentence}{evidence}\n"
                f"Recommended action: {recommendation}"
            )
        elif job.state == "SUCCEEDED":
            recommendation = "No corrective action is required for this job."
            answer = f"The {job.type} job succeeded.{file_sentence} {recommendation}"
        else:
            recommendation = "Wait for the current job to finish before diagnosing a failure."
            answer = (
                f"The {job.type} job is {job.state.lower()} at stage {job.stage} "
                f"({job.progress_percent}%).{file_sentence} {recommendation}"
            )

        reference = AssistantReference(
            type="VALIDATION",
            item_id=item.id,
            revision_id=revision.id,
            item_number=item.part_number,
            revision_code=revision.revision_code,
            label=(
                f"{item.part_number} revision {revision.revision_code} · {job.type} {job.state}"
            ),
        )
        return ToolResult(
            answer,
            [reference],
            [self._action(item, revision)],
            {
                "job_id": str(job.id),
                "revision_id": str(revision.id),
                "type": job.type,
                "state": job.state,
                "stage": job.stage,
                "progress_percent": job.progress_percent,
                "attempt_count": job.attempt_count,
                "errors": errors,
                "source_files": filenames,
                "recommended_action": recommendation,
            },
        )

    @staticmethod
    def _bom_rows(occurrences: list) -> list:
        rows = []
        for occurrence in occurrences:
            rows.append(occurrence)
            rows.extend(AssistantToolGateway._bom_rows(occurrence.children))
        return rows

    def _bom(self, item: Item, revision: Revision) -> ToolResult:
        bom = read_bom(revision.id, self.db, self.principal, 20)
        rows = self._bom_rows(bom.occurrences)
        lines = [
            f"- {row.child_part.part_number} — {row.child_part.name}, "
            f"revision {row.child_revision.revision_code}, quantity {row.quantity}"
            for row in rows[:25]
        ]
        answer = (
            f"{item.part_number}, revision {revision.revision_code}, contains "
            f"{len(rows)} occurrence(s)."
            + ("\n" + "\n".join(lines) if lines else " The BOM is empty.")
        )
        references = []
        seen = set()
        for row in rows:
            if row.child_revision.id in seen:
                continue
            seen.add(row.child_revision.id)
            references.append(
                AssistantReference(
                    type="ITEM",
                    item_id=row.child_part.id,
                    revision_id=row.child_revision.id,
                    item_number=row.child_part.part_number,
                    revision_code=row.child_revision.revision_code,
                    label=(
                        f"{row.child_part.part_number} — {row.child_part.name}, "
                        f"revision {row.child_revision.revision_code}"
                    ),
                )
            )
        return ToolResult(
            answer,
            references,
            [self._action(item, revision)],
            {
                "assembly_revision_id": str(revision.id),
                "occurrence_count": len(rows),
                "component_revision_ids": [str(row.child_revision.id) for row in rows],
            },
        )

    def _where_used(self, item: Item, revision: Revision) -> ToolResult:
        result = where_used(revision.id, self.db, self.principal)
        lines = [
            f"- {row.parent_part.part_number} — {row.parent_part.name}, "
            f"revision {row.parent_revision.revision_code}"
            for row in result.items
        ]
        answer = (
            f"Revision {revision.revision_code} of {item.part_number} is used in "
            f"{len(result.items)} assembly occurrence(s)."
            + ("\n" + "\n".join(lines) if lines else "")
        )
        references = [
            AssistantReference(
                type="ASSEMBLY",
                item_id=row.parent_part.id,
                revision_id=row.parent_revision.id,
                item_number=row.parent_part.part_number,
                revision_code=row.parent_revision.revision_code,
                label=(
                    f"{row.parent_part.part_number} — {row.parent_part.name}, "
                    f"revision {row.parent_revision.revision_code}"
                ),
            )
            for row in result.items
        ]
        return ToolResult(
            answer,
            references,
            [],
            {
                "revision_id": str(revision.id),
                "where_used_count": len(result.items),
                "assembly_revision_ids": [str(row.parent_revision.id) for row in result.items],
            },
        )

    @staticmethod
    def _viewer_search_terms(message: str) -> list[str]:
        words = re.findall(r"[a-z0-9][a-z0-9_-]*", message.casefold())
        ignored = {
            "all",
            "component",
            "components",
            "highlight",
            "isolate",
            "me",
            "occurrence",
            "occurrences",
            "only",
            "part",
            "parts",
            "please",
            "show",
            "the",
        }
        return [word[:-1] if word.endswith("s") else word for word in words if word not in ignored]

    @staticmethod
    def _occurrence_text(row) -> str:
        return " ".join(
            str(value or "")
            for value in (
                row.child_part.part_number,
                row.child_part.name,
                row.child_part.category,
                row.occurrence_key,
                row.source_reference,
                row.reference_designator,
            )
        ).casefold()

    def _viewer_matches(
        self,
        item: Item,
        revision: Revision,
        message: str,
        *,
        isolate: bool,
    ) -> ToolResult:
        if item.part_type != "ASSEMBLY":
            raise HTTPException(status_code=409, detail="Current item is not an assembly")
        terms = self._viewer_search_terms(message)
        if not terms:
            raise HTTPException(status_code=422, detail="Name the components to show")
        rows = self._bom_rows(read_bom(revision.id, self.db, self.principal, 20).occurrences)
        matched = [
            row for row in rows if all(term in self._occurrence_text(row) for term in terms)
        ][:100]
        if not matched:
            return ToolResult(
                f"I found no readable assembly occurrences matching {' '.join(terms)}.",
                [],
                [],
                {"terms": terms, "occurrence_ids": [], "match_count": 0},
            )
        action_type = "ISOLATE_OCCURRENCES" if isolate else "HIGHLIGHT_OCCURRENCES"
        action = AssistantViewerAction(
            type=action_type,
            occurrence_ids=[row.id for row in matched],
        )
        unique_components = {}
        for row in matched:
            unique_components.setdefault(row.child_revision.id, row)
        references = [
            AssistantReference(
                type="ITEM",
                item_id=row.child_part.id,
                revision_id=row.child_revision.id,
                item_number=row.child_part.part_number,
                revision_code=row.child_revision.revision_code,
                label=(
                    f"{row.child_part.part_number} — {row.child_part.name}, "
                    f"revision {row.child_revision.revision_code}"
                ),
            )
            for row in unique_components.values()
        ]
        verb = "Isolating" if isolate else "Highlighting"
        answer = f"{verb} {len(matched)} occurrence(s) matching {' '.join(terms)}."
        return ToolResult(
            answer,
            references,
            [action],
            {
                "terms": terms,
                "action": action_type,
                "match_count": len(matched),
                "occurrence_ids": [str(row.id) for row in matched],
            },
        )

    def _viewer_modified_components(self, item: Item, revision: Revision) -> ToolResult:
        if item.part_type != "ASSEMBLY":
            raise HTTPException(status_code=409, detail="Current item is not an assembly")
        revisions = self._visible_revisions(item)
        current_index = next(
            (index for index, row in enumerate(revisions) if row.id == revision.id),
            -1,
        )
        if current_index <= 0:
            return ToolResult(
                "There is no earlier readable assembly revision to compare.",
                [],
                [],
                {"revision_id": str(revision.id), "occurrence_ids": [], "match_count": 0},
            )
        previous = revisions[current_index - 1]
        current_rows = self._bom_rows(
            read_bom(revision.id, self.db, self.principal, 20).occurrences
        )
        previous_rows = self._bom_rows(
            read_bom(previous.id, self.db, self.principal, 20).occurrences
        )
        previous_by_key = {row.occurrence_key: row for row in previous_rows}
        modified = [
            row
            for row in current_rows
            if (old := previous_by_key.get(row.occurrence_key)) is None
            or old.child_revision.id != row.child_revision.id
            or old.quantity != row.quantity
        ][:100]
        if not modified:
            return ToolResult(
                f"No current components changed from revision {previous.revision_code}.",
                [],
                [],
                {
                    "revision_id": str(revision.id),
                    "previous_revision_id": str(previous.id),
                    "occurrence_ids": [],
                    "match_count": 0,
                },
            )
        action = AssistantViewerAction(
            type="HIGHLIGHT_OCCURRENCES",
            occurrence_ids=[row.id for row in modified],
        )
        return ToolResult(
            (
                f"Highlighting {len(modified)} current component occurrence(s) added or changed "
                f"since assembly revision {previous.revision_code}."
            ),
            [],
            [action],
            {
                "revision_id": str(revision.id),
                "previous_revision_id": str(previous.id),
                "match_count": len(modified),
                "occurrence_ids": [str(row.id) for row in modified],
            },
        )

    def _selected_occurrence_id(self, context: AssistantContext) -> uuid.UUID:
        raw = (
            context.assembly.selected_occurrence_id
            if context.assembly and context.assembly.selected_occurrence_id
            else context.selection.object_ids[0]
            if context.selection and context.selection.object_ids
            else None
        )
        if raw is None:
            raise HTTPException(status_code=422, detail="Select an assembly component first")
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Selected occurrence is invalid") from exc

    def _selected_component(
        self, item: Item, revision: Revision, context: AssistantContext
    ) -> ToolResult:
        if item.part_type != "ASSEMBLY":
            raise HTTPException(status_code=409, detail="Current item is not an assembly")
        occurrence_id = self._selected_occurrence_id(context)
        bom = read_bom(revision.id, self.db, self.principal, 20)
        selected = next(
            (row for row in self._bom_rows(bom.occurrences) if row.id == occurrence_id),
            None,
        )
        if selected is None:
            raise HTTPException(
                status_code=404,
                detail="Selected occurrence is not in the current readable assembly",
            )
        answer = (
            f"The selected occurrence is {selected.child_part.part_number} — "
            f"{selected.child_part.name}, revision {selected.child_revision.revision_code}. "
            f"Its quantity is {selected.quantity} and reference designator is "
            f"{selected.reference_designator or 'not recorded'}."
        )
        reference = AssistantReference(
            type="ITEM",
            item_id=selected.child_part.id,
            revision_id=selected.child_revision.id,
            item_number=selected.child_part.part_number,
            revision_code=selected.child_revision.revision_code,
            label=(
                f"{selected.child_part.part_number} — {selected.child_part.name}, "
                f"revision {selected.child_revision.revision_code}"
            ),
        )
        action = AssistantAction(
            type="OPEN_REVISION",
            item_id=selected.child_part.id,
            revision_id=selected.child_revision.id,
        )
        return ToolResult(
            answer,
            [reference],
            [action],
            {
                "occurrence_id": str(selected.id),
                "component_item_id": str(selected.child_part.id),
                "component_revision_id": str(selected.child_revision.id),
                "quantity": selected.quantity,
            },
        )

    def _selected_where_used(
        self, item: Item, revision: Revision, context: AssistantContext
    ) -> ToolResult:
        if item.part_type != "ASSEMBLY":
            raise HTTPException(status_code=409, detail="Current item is not an assembly")
        occurrence_id = self._selected_occurrence_id(context)
        selected = next(
            (
                row
                for row in self._bom_rows(
                    read_bom(revision.id, self.db, self.principal, 20).occurrences
                )
                if row.id == occurrence_id
            ),
            None,
        )
        if selected is None:
            raise HTTPException(
                status_code=404,
                detail="Selected occurrence is not in the current readable assembly",
            )
        result = self._where_used(selected.child_part, selected.child_revision)
        result.summary["selected_occurrence_id"] = str(selected.id)
        return result

    def _visible_revisions(self, item: Item) -> list[Revision]:
        revisions = list(
            self.db.scalars(
                select(Revision)
                .where(Revision.part_id == item.id)
                .order_by(Revision.created_at, Revision.revision_code)
            )
        )
        visible: list[Revision] = []
        for revision in revisions:
            try:
                visible.append(
                    readable_item_revision(self.db, item.id, revision.id, self.principal)
                )
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return visible

    def _revision_reference(self, item: Item, revision: Revision) -> AssistantReference:
        return AssistantReference(
            type="REVISION",
            item_id=item.id,
            revision_id=revision.id,
            item_number=item.part_number,
            revision_code=revision.revision_code,
            label=f"{item.part_number}, revision {revision.revision_code}",
        )

    def _revision_history(self, item: Item) -> ToolResult:
        revisions = self._visible_revisions(item)
        if not revisions:
            raise HTTPException(status_code=404, detail="No readable revisions found")
        lines = [
            f"- Revision {row.revision_code}: {row.state}, created {row.created_at.date()}"
            for row in reversed(revisions)
        ]
        answer = f"{item.part_number} has {len(revisions)} readable revision(s):\n" + "\n".join(
            lines
        )
        references = [self._revision_reference(item, row) for row in reversed(revisions)]
        return ToolResult(
            answer,
            references,
            [self._action(item, revisions[-1])],
            {"item_id": str(item.id), "revision_count": len(revisions)},
        )

    def _comparison_pair(
        self, item: Item, current: Revision, message: str
    ) -> tuple[Revision, Revision]:
        revisions = self._visible_revisions(item)
        by_code = {row.revision_code.casefold(): row for row in revisions}
        explicit = re.search(
            r"\bfrom\s+revision\s+([\w.-]+)\s+to\s+revision\s+([\w.-]+)\b"
            r"|\bfrom\s+([\w.-]+)\s+to\s+([\w.-]+)\b",
            message,
            re.IGNORECASE,
        )
        if explicit:
            groups = explicit.groups()
            from_code, to_code = (groups[0], groups[1]) if groups[0] else (groups[2], groups[3])
            before = by_code.get(from_code.casefold())
            after = by_code.get(to_code.casefold())
            if before is None or after is None:
                raise HTTPException(status_code=404, detail="Requested revision was not found")
            return before, after
        try:
            index = next(index for index, row in enumerate(revisions) if row.id == current.id)
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="Current revision was not found") from exc
        if index == 0:
            raise HTTPException(status_code=409, detail="Current revision has no previous revision")
        return revisions[index - 1], current

    def _recorded_reason(self, revision: Revision) -> str | None:
        for source in (revision.metadata_json or {}, revision.derived_metadata_json or {}):
            for key in ("change_reason", "revision_reason", "reason", "comment"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        audits = self.db.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == revision.id)
            .order_by(AuditLog.occurred_at.desc())
        )
        for audit in audits:
            for key in ("change_reason", "reason", "comment"):
                value = audit.details_json.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _compare_revisions(self, item: Item, current: Revision, message: str) -> ToolResult:
        before, after = self._comparison_pair(item, current, message)
        comparison, _ = create_comparison(
            self.db,
            self.principal,
            item.id,
            before.id,
            after.id,
            ["METADATA", "PHYSICAL_PROPERTIES", "FILES"],
        )
        structured = comparison_response(self.db, comparison)
        wants_explanation = bool(re.search(r"\b(why|explain)\b", message, re.IGNORECASE))
        if wants_explanation:
            if self.settings is None:
                raise HTTPException(status_code=503, detail="Assistant settings are unavailable")
            explanation = explain_comparison(
                self.db,
                self.principal,
                self.settings,
                comparison,
                "ENGINEERING",
            )
            explained = explanation_response(explanation)
            facts = [
                f"- {fact.text} [change:{', '.join(fact.change_keys)}]" for fact in explained.facts
            ]
            answer = "\n".join(
                [explained.summary, *facts, explained.cause.text, *explained.advisory]
            )
            return ToolResult(
                answer,
                [
                    self._revision_reference(item, before),
                    self._revision_reference(item, after),
                ],
                [self._comparison_action(item, before, after, comparison.id)],
                {
                    "comparison_id": str(comparison.id),
                    "explanation_id": str(explanation.id),
                    "comparison_result_hash": explanation.comparison_result_hash,
                    "mode": explanation.mode,
                    "cited_change_keys": explanation.cited_change_keys_json,
                    "cited_source_ids": explanation.cited_source_ids_json,
                    "from_revision_id": str(before.id),
                    "to_revision_id": str(after.id),
                },
            )
        domains = {row.domain: row for row in structured.domains}
        metadata_rows = domains["METADATA"].changes + domains["PHYSICAL_PROPERTIES"].changes
        file_rows = domains["FILES"].changes
        metadata_changes = [
            {"field": row.property_path, "old": row.old_value, "new": row.new_value}
            for row in metadata_rows
        ]
        file_changes = [
            {
                "kind": row.metadata.get("kind"),
                "filename": row.metadata.get("filename"),
                "change": row.change_type.casefold(),
            }
            for row in file_rows
        ]
        reason = self._recorded_reason(after)
        facts = []
        if before.state != after.state:
            facts.append(f"state changed from {before.state} to {after.state}")
        facts.append(f"{len(metadata_changes)} metadata field(s) changed")
        facts.append(f"{len(file_changes)} file(s) added, removed, or modified")
        details = [
            f"- {change['field']}: {change['old']} → {change['new']}"
            for change in metadata_changes[:8]
        ]
        details.extend(
            f"- {change['filename']} ({change['kind']}): {change['change']}"
            for change in file_changes[:8]
        )
        answer = (
            f"Revision {before.revision_code} to {after.revision_code}: "
            + "; ".join(facts)
            + "."
            + ("\n" + "\n".join(details) if details else "")
            + "\n"
            + (f"Recorded reason: {reason}." if reason else "No change reason is recorded.")
        )
        summary = {
            "comparison_id": str(comparison.id),
            "comparison_engine_version": comparison.comparison_engine_version,
            "item_id": str(item.id),
            "from_revision_id": str(before.id),
            "to_revision_id": str(after.id),
            "metadata_changes": metadata_changes,
            "file_changes": file_changes,
            "state_change": (
                {"old": before.state, "new": after.state} if before.state != after.state else None
            ),
            "recorded_reason": reason,
        }
        return ToolResult(
            answer,
            [self._revision_reference(item, before), self._revision_reference(item, after)],
            [self._comparison_action(item, before, after, comparison.id)],
            summary,
        )

    def _assembly_features(self, item, revision, features) -> ToolResult:
        report = AssemblyFeatureSearch(self.db, self.principal).count(revision.id, features)
        references = [self._reference(item, revision, "METADATA")]
        actions = [self._action(item, revision)]
        seen = {revision.id}
        for row in report.components:
            child_id = uuid.UUID(row["revision_id"])
            if child_id in seen or len(references) >= 25:
                continue
            seen.add(child_id)
            references.append(AssistantReference(
                type="METADATA", item_id=uuid.UUID(row["item_id"]), revision_id=child_id,
                item_number=row["item_number"], revision_code=row["revision_code"],
                label=f"{row['item_number']}, revision {row['revision_code']}",
            ))
            actions.append(AssistantAction(
                type="OPEN_REVISION", item_id=uuid.UUID(row["item_id"]), revision_id=child_id,
            ))
        return ToolResult(report.text(), references, actions, report.summary())

    def _discover_features(self, features, message) -> ToolResult:
        reports, paths, warnings = AssemblyFeatureSearch(self.db, self.principal).discover(
            features, match_any=bool(re.search(r"\bor\b", message, re.I)),
        )
        answer = "Matching part and assembly revisions from authorized feature evidence."
        answer += "\n" + "\n\n".join(report.text() for report in reports) if reports else (
            " No matches were confirmed in the inspected evidence; missing coverage is not absence."
        )
        if warnings:
            answer += "\n" + "\n".join(sorted(warnings))
        return ToolResult(
            answer,
            [self._reference(r.item, r.revision, "METADATA") for r in reports],
            [self._action(r.item, r.revision) for r in reports],
            {"results": [r.summary() for r in reports],
             "discovery_paths": {str(key): value for key, value in paths.items()
                                 if any(r.revision.id == key for r in reports)},
             "warnings": sorted(warnings)},
        )

    def _item_features(
        self, item: Item, revision: Revision, features: list[str]
    ) -> ToolResult:
        extraction = (
            self.db.get(MetadataExtraction, revision.current_metadata_extraction_id)
            if revision.current_metadata_extraction_id else None
        )
        snapshot_id = latest_content_snapshot_id(self.db, revision.id)
        usable = bool(
            extraction
            and extraction.revision_id == revision.id
            and extraction.status in {"READY", "PARTIAL"}
            and (snapshot_id is None or extraction.snapshot_id == snapshot_id)
        )
        metadata = extraction.metadata_json if usable else {}
        quality = metadata.get("quality", {}).get("features")
        feature_section = metadata.get("extraction", {}).get("sections", {}).get("features", {})
        section_status = feature_section.get("status")
        unsupported = {"unavailable", "unsupported", "failed", "not_applicable"}
        usable = usable and quality not in unsupported and section_status not in unsupported
        evidence = feature_evidence(extraction, revision.id, snapshot_id, features)
        subject = f"revision {revision.revision_code} of {item.part_number}"
        answers: list[str] = []
        summary = {
            "item_id": str(item.id), "revision_id": str(revision.id),
            "feature_quality": quality, "scope": item.part_type,
        }
        for feature in features:
            supported = feature in _SUPPORTED_FEATURES
            count = evidence[feature].count
            complete = evidence[feature].complete
            plural = f"{feature}es" if feature == "boss" else f"{feature}s"
            presence = "unknown"
            if not supported:
                answers.append(
                    f"{plural.title()} are not supported by the current normalized feature "
                    "metadata, so their presence is unknown."
                )
            elif count is not None and count > 0:
                qualifier = "" if complete else "at least "
                answers.append(
                    f"Recorded metadata reports {qualifier}{count} {feature}(s) for {subject}."
                )
                presence = "present"
            elif count == 0 and complete:
                answers.append(f"The extraction explicitly records zero {plural} for {subject}.")
                presence = "absent"
            else:
                answers.append(
                    f"I cannot determine whether {subject} has {plural} from the current metadata. "
                    f"Missing or incomplete {feature} information does not mean "
                    f"there are no {plural}."
                )
            summary[f"{feature}_count"] = count
            summary[f"{feature}_presence"] = presence
        answer = " ".join(answers)
        details = (
            metadata.get("features", {}).get("holes", [])
            if usable and "hole" in features else []
        )
        if details:
            diameters = sorted({
                hole["diameter_mm"] for hole in details
                if isinstance(hole, dict) and hole.get("diameter_mm") is not None
            })
            if diameters:
                answer += " Recorded hole diameters: " + ", ".join(
                    f"{diameter:g} mm" for diameter in diameters
                ) + "."
        if item.part_type == "ASSEMBLY":
            answer += (
                " This is assembly-level evidence; it does not establish whether individual "
                "components have these features. Open the relevant component revision "
                "to check its metadata."
            )
        return ToolResult(
            answer,
            [self._reference(item, revision, "METADATA")],
            [self._action(item, revision)],
            summary,
        )

    def _item_material(self, item: Item, revision: Revision) -> ToolResult:
        extraction = (
            self.db.get(MetadataExtraction, revision.current_metadata_extraction_id)
            if revision.current_metadata_extraction_id
            else None
        )
        material = extraction.material if extraction else None
        if not material:
            properties = (revision.derived_metadata_json or {}).get("properties", {})
            material = properties.get("material")
        if not material:
            properties = (revision.metadata_json or {}).get("properties", {})
            material = properties.get("material")
        answer = (
            f"Revision {revision.revision_code} of {item.part_number} uses {material}."
            if material
            else (
                f"No material is recorded for revision {revision.revision_code} "
                f"of {item.part_number}."
            )
        )
        summary = {
            "item_id": str(item.id),
            "revision_id": str(revision.id),
            "material": material,
        }
        return ToolResult(
            answer,
            [self._reference(item, revision, "METADATA")],
            [self._action(item, revision)],
            summary,
        )
