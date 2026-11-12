import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.knowledge.models import KnowledgeFilter, KnowledgeSearchRequest
from modelryx.knowledge.service import search_knowledge
from modelryx.models import (
    Revision,
    RevisionComparison,
    RevisionComparisonDomain,
    RevisionComparisonExplanation,
)
from modelryx.services.ai import AiGateway

from .models import (
    ComparisonExplanationResponse,
    ExplanationCause,
    ExplanationFact,
    ExplanationLevel,
)
from .service import authorize_comparison, comparison_response

PROMPT_VERSION = "phase5-explanation-prompt-v1"
CONTRACT_VERSION = "phase5-explanation-contract-v1"
LEVEL_LIMITS = {"QUICK": 8, "ENGINEERING": 30, "DETAILED": 100}


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _result_hash(db: Session, comparison: RevisionComparison) -> str:
    domains = db.execute(
        select(
            RevisionComparisonDomain.domain,
            RevisionComparisonDomain.status,
            RevisionComparisonDomain.result_hash,
        )
        .where(RevisionComparisonDomain.comparison_id == comparison.id)
        .order_by(RevisionComparisonDomain.domain)
    ).all()
    return _hash(
        {
            "comparison_id": str(comparison.id),
            "engine": comparison.comparison_engine_version,
            "domains": [tuple(row) for row in domains],
        }
    )


def _evidence(
    db: Session,
    principal: Principal,
    settings: Settings,
    comparison: RevisionComparison,
    paths: list[str],
):
    revision = db.get(Revision, comparison.to_revision_id)
    reason = ""
    if revision is not None:
        for source in (revision.metadata_json or {}, revision.derived_metadata_json or {}):
            for key in ("change_reason", "revision_reason", "reason", "comment"):
                if isinstance(source.get(key), str):
                    reason = source[key]
                    break
            if reason:
                break
    query = "revision change reason " + reason + " " + " ".join(paths[:12])
    return search_knowledge(
        db,
        principal,
        settings,
        KnowledgeSearchRequest(
            query=query[:4000],
            filters=KnowledgeFilter(
                item_ids=[comparison.item_id],
                revision_ids=[comparison.from_revision_id, comparison.to_revision_id],
            ),
            limit=min(settings.ai_retrieval_limit, 8),
        ),
    )


def _deterministic_output(structured, evidence, level: ExplanationLevel) -> dict:
    completed = [row for row in structured.domains if row.status in {"COMPLETED", "UNCHANGED"}]
    changes = [change for domain in completed for change in domain.changes]
    limit = LEVEL_LIMITS[level]
    selected = changes[:limit]
    facts = [
        {
            "text": (
                f"{change.property_path} was {change.change_type.casefold()} "
                f"from {change.old_value!r} to {change.new_value!r}."
            ),
            "change_keys": [change.change_key],
        }
        for change in selected
    ]
    evidence_ids = [str(row.source_id) for row in evidence.results]
    cause = (
        {
            "text": "Authorized recorded evidence is available for review of the change cause.",
            "source_ids": evidence_ids,
            "recorded": True,
        }
        if evidence_ids
        else {
            "text": "No authorized recorded cause was found.",
            "source_ids": [],
            "recorded": False,
        }
    )
    changed_domains = sorted({domain.domain for domain in completed if domain.changes})
    summary = (
        f"{len(changes)} deterministic change(s) were found across "
        f"{', '.join(changed_domains) if changed_domains else 'no changed domains'}."
    )
    advisory = []
    if any(change.significance in {"MAJOR", "CRITICAL"} for change in changes):
        advisory.append("Advisory: review major or critical changes before engineering approval.")
    warnings = list(evidence.warnings)
    for domain in structured.domains:
        if domain.status in {"UNSUPPORTED", "FAILED"}:
            warnings.append(f"{domain.domain} domain is {domain.status.lower()}.")
        if any(change.confidence < 0.8 for change in domain.changes):
            warnings.append(f"{domain.domain} contains low-confidence changes requiring review.")
    if len(changes) > limit:
        warnings.append(f"Explanation is bounded to {limit} of {len(changes)} changes.")
    return {
        "summary": summary,
        "facts": facts,
        "cause": cause,
        "advisory": advisory,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _ai_output(gateway: AiGateway, payload: dict) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "facts", "cause", "advisory"],
        "properties": {
            "summary": {"type": "string"},
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "change_keys"],
                    "properties": {
                        "text": {"type": "string"},
                        "change_keys": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "cause": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "source_ids", "recorded"],
                "properties": {
                    "text": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "recorded": {"type": "boolean"},
                },
            },
            "advisory": {"type": "array", "items": {"type": "string"}},
        },
    }
    return gateway.structured_chat(
        system=(
            "Explain only the supplied deterministic Modelryx changes. Every factual statement "
            "must cite one or more supplied change_keys. A recorded cause must cite supplied "
            "source_ids; otherwise say no recorded cause was found. Prefix every interpretation "
            "or recommendation with 'Advisory:'. Do not convert unsupported or uncertain domains "
            "into facts."
        ),
        payload=payload,
        schema=schema,
        schema_name="modelryx_revision_explanation",
    )


def _valid_ai_output(output: dict, change_keys: set[str], source_ids: set[str]) -> bool:
    facts = output.get("facts")
    cause = output.get("cause")
    advisory = output.get("advisory")
    if not isinstance(facts, list) or not isinstance(cause, dict) or not isinstance(advisory, list):
        return False
    if any(
        not row.get("change_keys") or not set(row["change_keys"]).issubset(change_keys)
        for row in facts
        if isinstance(row, dict)
    ) or any(not isinstance(row, dict) for row in facts):
        return False
    cited_sources = set(cause.get("source_ids", []))
    if cause.get("recorded") and (not cited_sources or not cited_sources.issubset(source_ids)):
        return False
    return all(str(text).startswith("Advisory:") for text in advisory)


def explain_comparison(
    db: Session,
    principal: Principal,
    settings: Settings,
    comparison: RevisionComparison,
    level: ExplanationLevel,
) -> RevisionComparisonExplanation:
    authorize_comparison(db, principal, comparison)
    structured = comparison_response(db, comparison)
    completed_changes = [
        change
        for domain in structured.domains
        if domain.status in {"COMPLETED", "UNCHANGED"}
        for change in domain.changes
    ]
    evidence = _evidence(
        db,
        principal,
        settings,
        comparison,
        [change.property_path for change in completed_changes],
    )
    result_hash = _result_hash(db, comparison)
    evidence_hash = _hash(
        [(str(row.source_id), str(row.chunk_id), row.snippet) for row in evidence.results]
    )
    gateway = AiGateway(settings)
    desired_mode = "AI" if gateway.chat_enabled else "DETERMINISTIC"
    cache_key = _hash(
        {
            "result": result_hash,
            "evidence": evidence_hash,
            "level": level,
            "prompt": PROMPT_VERSION,
            "contract": CONTRACT_VERSION,
            "mode": desired_mode,
            "model": settings.ai_chat_model if desired_mode == "AI" else None,
        }
    )
    existing = db.scalar(
        select(RevisionComparisonExplanation).where(
            RevisionComparisonExplanation.cache_key == cache_key
        )
    )
    if existing is not None:
        return existing

    fallback = _deterministic_output(structured, evidence, level)
    output = fallback
    mode = "DETERMINISTIC"
    warnings = list(fallback["warnings"])
    if gateway.chat_enabled:
        limit = LEVEL_LIMITS[level]
        payload = {
            "comparison_id": str(comparison.id),
            "comparison_engine_version": comparison.comparison_engine_version,
            "level": level,
            "changes": [
                {
                    "change_key": row.change_key,
                    "domain": domain.domain,
                    "change_type": row.change_type,
                    "property_path": row.property_path,
                    "old_value": row.old_value,
                    "new_value": row.new_value,
                    "unit": row.unit,
                    "significance": row.significance,
                    "confidence": row.confidence,
                }
                for domain in structured.domains
                if domain.status in {"COMPLETED", "UNCHANGED"}
                for row in domain.changes
            ][:limit],
            "domain_states": [
                {"domain": row.domain, "status": row.status, "warnings": row.warnings}
                for row in structured.domains
            ],
            "unchanged_domains": [
                row.domain for row in structured.domains if row.status == "UNCHANGED"
            ],
            "evidence": [
                {
                    "source_id": str(row.source_id),
                    "title": row.title,
                    "snippet": row.snippet,
                    "location": row.location.model_dump(mode="json"),
                }
                for row in evidence.results
            ],
        }
        try:
            candidate = _ai_output(gateway, payload)
            valid_keys = {row["change_key"] for row in payload["changes"]}
            valid_sources = {row["source_id"] for row in payload["evidence"]}
            if _valid_ai_output(candidate, valid_keys, valid_sources):
                output = {**candidate, "warnings": warnings}
                mode = "AI"
            else:
                warnings.append("AI output failed citation validation; deterministic summary used.")
        except HTTPException:
            warnings.append("AI generation failed; deterministic summary used.")
    else:
        warnings.append("AI gateway is disabled; deterministic summary used.")
    output["warnings"] = list(dict.fromkeys(warnings))
    actual_cache_key = _hash(
        {
            "result": result_hash,
            "evidence": evidence_hash,
            "level": level,
            "prompt": PROMPT_VERSION,
            "contract": CONTRACT_VERSION,
            "mode": mode,
            "model": settings.ai_chat_model if mode == "AI" else None,
        }
    )
    if actual_cache_key != cache_key:
        existing = db.scalar(
            select(RevisionComparisonExplanation).where(
                RevisionComparisonExplanation.cache_key == actual_cache_key
            )
        )
        if existing is not None:
            return existing
        cache_key = actual_cache_key
    cited_change_keys = sorted(
        {key for fact in output["facts"] for key in fact.get("change_keys", [])}
    )
    cited_source_ids = sorted(set(output["cause"].get("source_ids", [])))
    user = user_for_principal(db, principal)
    explanation = RevisionComparisonExplanation(
        comparison_id=comparison.id,
        comparison_result_hash=result_hash,
        level=level,
        mode=mode,
        prompt_version=PROMPT_VERSION,
        contract_version=CONTRACT_VERSION,
        provider="openai-compatible" if mode == "AI" else "deterministic",
        model=settings.ai_chat_model or None,
        cache_key=cache_key,
        output_json=output,
        cited_change_keys_json=cited_change_keys,
        cited_source_ids_json=cited_source_ids,
        warnings_json=output["warnings"],
        created_by=user.id,
        generated_at=datetime.now(UTC),
    )
    db.add(explanation)
    db.flush()
    return explanation


def explanation_response(
    explanation: RevisionComparisonExplanation,
) -> ComparisonExplanationResponse:
    output = explanation.output_json
    return ComparisonExplanationResponse(
        id=explanation.id,
        comparison_id=explanation.comparison_id,
        comparison_result_hash=explanation.comparison_result_hash,
        level=explanation.level,
        mode=explanation.mode,
        prompt_version=explanation.prompt_version,
        contract_version=explanation.contract_version,
        provider=explanation.provider,
        model=explanation.model,
        summary=output["summary"],
        facts=[ExplanationFact.model_validate(row) for row in output["facts"]],
        cause=ExplanationCause.model_validate(output["cause"]),
        advisory=output["advisory"],
        cited_change_keys=explanation.cited_change_keys_json,
        cited_source_ids=explanation.cited_source_ids_json,
        warnings=explanation.warnings_json,
        generated_at=explanation.generated_at,
    )
