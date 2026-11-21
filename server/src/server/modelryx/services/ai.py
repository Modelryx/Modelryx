import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from modelryx.core.auth import Principal
from modelryx.core.config import Settings
from modelryx.duplicate_detection.search_document import (
    SEARCH_DOCUMENT_VERSION,
    build_semantic_text,
)
from modelryx.metadata.array_quality import ARRAY_GEOMETRY_WARNING, has_unverified_array_geometry
from modelryx.models import AiDocument, Item, Revision
from modelryx.services.ai_adapters import create_chat_adapter, post_json

_QUERY_FILLER = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "contain",
    "containing",
    "contains",
    "find",
    "for",
    "give",
    "has",
    "have",
    "in",
    "item",
    "items",
    "list",
    "me",
    "of",
    "part",
    "parts",
    "please",
    "show",
    "that",
    "the",
    "which",
    "with",
}
logger = logging.getLogger("modelryx.ai")


@dataclass
class RetrievedDocument:
    document: AiDocument
    score: float
    item_number: str
    revision_code: str


class AiGateway:
    """Provider-neutral chat facade with a separate compatible embedding gateway."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.chat_adapter = create_chat_adapter(settings)

    @property
    def embeddings_enabled(self) -> bool:
        return bool(self.settings.ai_gateway_base_url and self.settings.ai_embedding_model)

    @property
    def chat_enabled(self) -> bool:
        return self.chat_adapter.enabled

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.ai_gateway_base_url:
            raise HTTPException(status_code=503, detail="AI gateway is not configured")
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_gateway_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_gateway_api_key}"
        return post_json(
            f"{self.settings.ai_gateway_base_url.rstrip('/')}/{path.lstrip('/')}",
            payload, headers, self.settings.ai_request_timeout_seconds,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._post(
            "/embeddings",
            {
                "model": self.settings.ai_embedding_model,
                "input": texts,
                "dimensions": self.settings.ai_embedding_dimensions,
            },
        )
        vectors = [
            entry["embedding"]
            for entry in sorted(response.get("data", []), key=lambda row: row["index"])
        ]
        if len(vectors) != len(texts):
            raise HTTPException(
                status_code=502,
                detail="AI gateway returned an incomplete embedding response",
            )
        if any(len(vector) != self.settings.ai_embedding_dimensions for vector in vectors):
            raise HTTPException(
                status_code=502,
                detail="Embedding dimensions do not match configuration",
            )
        return vectors

    def answer(self, question: str, sources: list[RetrievedDocument]) -> str:
        source_text = "\n\n".join(
            f"[S{index}] {source.document.title}\n{source.document.content}"
            for index, source in enumerate(sources, 1)
        )
        result = self.chat_adapter.complete(
            system=(
                "You are the Modelryx engineering retrieval agent. Answer only from "
                "the supplied sources. Cite factual claims with [S#]. If evidence is "
                "insufficient, say what is missing. Do not invent dimensions, "
                "materials, relationships, or lifecycle states. Multiple sources can "
                "describe CAD hierarchy nodes from the same Modelryx item and "
                "revision—for example, a PartDesign Body and its AdditiveBox feature. "
                "Treat those as supporting evidence for one item, not separate item "
                "records. When listing or counting items, deduplicate by the Item and "
                "Revision fields in the source content."
            ),
            user=f"Question: {question}\n\nSources:\n{source_text}",
        )
        if not isinstance(result, str):
            raise HTTPException(502, "AI gateway returned an invalid chat response")
        return result

    def structured_chat(
        self,
        *,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        schema_name: str = "modelryx_structured_output",
    ) -> dict[str, Any]:
        """Request structured data and validate it locally for every provider."""
        result = self.chat_adapter.complete(
            system=system,
            user=json.dumps(payload, separators=(",", ":")),
            schema=schema,
            schema_name=schema_name,
        )
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            if not isinstance(parsed, dict):
                raise ValueError("expected an object")
            Draft202012Validator(schema).validate(parsed)
            return parsed
        except (TypeError, ValueError, ValidationError):
            raise HTTPException(
                status_code=502, detail="AI gateway returned invalid structured output"
            ) from None



def _value(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={_value(entry)}" for key, entry in sorted(value.items()))
    if isinstance(value, list):
        return ", ".join(_value(entry) for entry in value)
    return str(value)


def _lexical_terms(question: str) -> list[str]:
    """Extract useful terms for broad first-stage CAD retrieval."""
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]+", question.casefold()):
        if token not in _QUERY_FILLER and token not in terms:
            terms.append(token)
    return terms[:20]


def build_revision_documents(item: Item, revision: Revision) -> list[dict[str, Any]]:
    bom = revision.derived_metadata_json or {}
    nodes = bom.get("items", []) if isinstance(bom.get("items"), list) else []
    part_metadata = bom.get("partMetadata") if isinstance(bom.get("partMetadata"), dict) else None
    uncertain_array_geometry = bool(part_metadata and has_unverified_array_geometry(part_metadata))
    geometry_warning_lines = (
        [f"Geometry evidence warning: {ARRAY_GEOMETRY_WARNING}"] if uncertain_array_geometry else []
    )
    geometry_warning_payload = (
        {"geometry_evidence_warning": ARRAY_GEOMETRY_WARNING} if uncertain_array_geometry else {}
    )
    materials = sorted(
        {
            str(node.get("material"))
            for node in nodes
            if isinstance(node, dict) and node.get("material")
        }
    )
    part_material = (
        part_metadata.get("physical", {}).get("material", {}).get("name") if part_metadata else None
    )
    if part_material and str(part_material) not in materials:
        materials.append(str(part_material))
        materials.sort()
    summary = "\n".join(
        [
            f"Item: {item.part_number} — {item.name}",
            f"Revision: {revision.revision_code}",
            f"Item type: {item.part_type}",
            f"Lifecycle state: {revision.lifecycle_state}",
            f"CAD system: {revision.cad_system}",
            f"Native format: {revision.native_format}",
            f"Description: {item.description or 'Not provided'}",
            f"Category: {item.category or 'Not provided'}",
            f"BOM node count: {len(nodes)}",
            f"Materials: {', '.join(materials) if materials else 'Not specified'}",
            *geometry_warning_lines,
            f"PartMetadata: {_value(part_metadata) if part_metadata else 'Not available'}",
            f"Uploaded metadata: {_value(revision.metadata_json)}",
        ]
    )
    documents = [
        {
            "chunk_key": "revision-summary",
            "title": f"{item.part_number} revision {revision.revision_code} summary",
            "content": summary,
            "metadata": {"kind": "revision", "itemNumber": item.part_number},
        }
    ]
    if part_metadata:
        documents.append(
            {
                "chunk_key": "part-metadata-v1",
                "title": f"{item.part_number} engineering metadata",
                "content": build_semantic_text(part_metadata),
                "metadata": {
                    "kind": "part-metadata",
                    "schema": part_metadata.get("schema"),
                    "schemaVersion": part_metadata.get("schema_version"),
                    "searchDocumentVersion": SEARCH_DOCUMENT_VERSION,
                    "itemNumber": item.part_number,
                },
            }
        )
        physical = part_metadata.get("physical", {})
        geometry = part_metadata.get("geometry", {})
        if physical or geometry:
            documents.append(_metadata_chunk(
                item, revision, "physical-properties", "physical properties",
                {
                    **geometry_warning_payload,
                    "physical": physical, "geometry": geometry,
                },
            ))
        features = part_metadata.get("features", {})
        native = part_metadata.get("native", {})
        parameters = native.get("parameters", []) if isinstance(native, dict) else []
        parameter_graph = native.get("parameter_graph", {}) if isinstance(native, dict) else {}
        configurations = native.get("configurations", {}) if isinstance(native, dict) else {}
        reference_geometry = (
            native.get("reference_geometry", []) if isinstance(native, dict) else []
        )
        if features or parameters or parameter_graph or configurations or reference_geometry:
            documents.append(_metadata_chunk(
                item, revision, "features-parameters", "features and parameters",
                {
                    "features": features,
                    "parameters": parameters,
                    "parameter_graph": parameter_graph,
                    "configurations": configurations,
                    "reference_geometry": reference_geometry,
                },
            ))
        assembly = part_metadata.get("assembly", {})
        bom_views = native.get("bom_views", []) if isinstance(native, dict) else []
        occurrences = native.get("occurrences", []) if isinstance(native, dict) else []
        assembly_joints = native.get("assembly_joints", []) if isinstance(native, dict) else []
        if assembly or bom_views or occurrences or assembly_joints:
            documents.append(_metadata_chunk(
                item, revision, "assembly-bom", "assembly and BOM",
                {
                    **geometry_warning_payload,
                    "assembly": assembly,
                    "bom_views": bom_views,
                    "occurrences": occurrences,
                    "assembly_joints": assembly_joints,
                },
            ))
        drawings = part_metadata.get("drawings", [])
        if drawings:
            documents.append(_metadata_chunk(
                item, revision, "drawings", "drawing metadata", {"drawings": drawings},
            ))
        quality = part_metadata.get("quality", {})
        if quality:
            documents.append(_metadata_chunk(
                item, revision, "extraction-quality", "metadata extraction quality",
                {"quality": quality},
            ))
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        source = node.get("source") if isinstance(node.get("source"), dict) else {}
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        lines = [
            f"Item: {item.part_number}",
            f"Revision: {revision.revision_code}",
            f"Object name: {node.get('name', 'Unnamed')}",
            f"Object type: {node.get('nodeType', 'unknown')}",
            f"Occurrence ID: {node.get('occurrenceId', 'unknown')}",
            f"Parent occurrence ID: {node.get('parentOccurrenceId', 'root')}",
            f"Material: {node.get('material', 'Not specified')}",
            f"Source object: {source.get('objectLabel') or source.get('objectName') or 'unknown'}",
            f"Source type: {source.get('typeId', 'unknown')}",
            f"Placement: {_value(node.get('placement', 'Not specified'))}",
            f"Display: {_value(node.get('display', {}))}",
            *geometry_warning_lines,
            f"Properties: {_value(properties)}",
        ]
        occurrence_id = str(node.get("occurrenceId") or index)
        documents.append(
            {
                "chunk_key": f"bom-node:{occurrence_id}",
                "title": f"{item.part_number}: {node.get('name', 'Unnamed object')}",
                "content": "\n".join(lines),
                "metadata": {
                    "kind": "bom-node",
                    "occurrenceId": node.get("occurrenceId"),
                    "nodeType": node.get("nodeType"),
                },
            }
        )
    return documents


def _metadata_chunk(
    item: Item,
    revision: Revision,
    chunk_key: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "chunk_key": chunk_key,
        "title": f"{item.part_number} {label}",
        "content": "\n".join([
            f"Item: {item.part_number}",
            f"Revision: {revision.revision_code}",
            f"CAD system: {revision.cad_system}",
            f"{label.title()}: {_value(payload)}",
        ]),
        "metadata": {"kind": chunk_key, "itemNumber": item.part_number},
    }


def index_revision(db: Session, revision: Revision, settings: Settings) -> int:
    item = db.get(Item, revision.part_id)
    documents = build_revision_documents(item, revision)
    gateway = AiGateway(settings)
    vectors: list[list[float] | None] = []
    if gateway.embeddings_enabled:
        try:
            # Keep requests bounded for large assemblies and gateways with input limits.
            for offset in range(0, len(documents), 64):
                batch = documents[offset : offset + 64]
                vectors.extend(gateway.embed([document["content"] for document in batch]))
        except HTTPException:
            logger.exception(
                "embedding_generation_failed revision_id=%s; storing lexical documents",
                revision.id,
            )
            vectors = [None] * len(documents)
    else:
        vectors = [None] * len(documents)
    db.execute(delete(AiDocument).where(AiDocument.revision_id == revision.id))
    for document, vector in zip(documents, vectors, strict=True):
        content = document["content"]
        db.add(
            AiDocument(
                item_id=item.id,
                revision_id=revision.id,
                chunk_key=document["chunk_key"],
                title=document["title"],
                content=content,
                metadata_json={
                    **document["metadata"],
                    "embeddingVersion": "1" if vector is not None else None,
                    "embeddingDimension": len(vector) if vector is not None else None,
                },
                embedding=vector,
                embedding_model=settings.ai_embedding_model or None,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                indexed_at=datetime.now(UTC),
            )
        )
    db.commit()
    return len(documents)


def retrieve(
    db: Session,
    question: str,
    principal: Principal,
    settings: Settings,
    item_id: uuid.UUID | None = None,
    revision_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[RetrievedDocument]:
    limit = limit or settings.ai_retrieval_limit
    allowed = Revision.state.in_(["READY", "READY_WITH_WARNINGS"])
    if principal.role != "administrator":
        from modelryx.core.audit import user_for_principal

        user = user_for_principal(db, principal)
        allowed = or_(allowed, Item.created_by == user.id)
    filters = [allowed]
    if item_id:
        filters.append(AiDocument.item_id == item_id)
    if revision_id:
        filters.append(AiDocument.revision_id == revision_id)

    base = (
        select(AiDocument, Item.part_number, Revision.revision_code)
        .join(Item, Item.id == AiDocument.item_id)
        .join(Revision, Revision.id == AiDocument.revision_id)
        .where(*filters)
    )
    ranked: dict[uuid.UUID, tuple[AiDocument, str, str, float]] = {}
    terms = _lexical_terms(question)
    # OR is intentional for first-stage retrieval. The rank and the answering model
    # handle precision, while natural-language filler must not make recall zero.
    lexical_query = func.to_tsquery("english", " | ".join(terms)) if terms else None
    lexical_rank = (
        func.ts_rank_cd(func.to_tsvector("english", AiDocument.content), lexical_query)
        if lexical_query is not None
        else None
    )
    if lexical_rank is not None:
        for rank, (document, number, code) in enumerate(
            db.execute(
                base.where(lexical_rank > 0).order_by(lexical_rank.desc()).limit(limit * 2)
            ).all(),
            1,
        ):
            ranked[document.id] = (document, number, code, 1 / (60 + rank))

    gateway = AiGateway(settings)
    if gateway.embeddings_enabled:
        query_vector = gateway.embed([question])[0]
        vector_base = base.where(AiDocument.embedding.is_not(None))
        for rank, (document, number, code) in enumerate(
            db.execute(
                vector_base.order_by(AiDocument.embedding.cosine_distance(query_vector)).limit(
                    limit * 2
                )
            ).all(),
            1,
        ):
            previous = ranked.get(document.id)
            score = (previous[3] if previous else 0) + 1 / (60 + rank)
            ranked[document.id] = (document, number, code, score)
    return [
        RetrievedDocument(document, score, number, code)
        for document, number, code, score in sorted(
            ranked.values(), key=lambda entry: entry[3], reverse=True
        )[:limit]
    ]
