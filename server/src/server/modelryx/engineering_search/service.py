import time
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import Float, String, and_, case, cast, func, literal, literal_column, or_, select
from sqlalchemy.orm import Session, aliased

from modelryx.core.audit import user_for_principal
from modelryx.core.auth import Principal
from modelryx.engineering_search.catalog_loader import active_scenario_catalog
from modelryx.engineering_search.models import (
    EngineeringSearchItem,
    EngineeringSearchResponse,
    MatchReason,
    NumericPredicate,
    SearchCoverage,
    SearchRequest,
    SearchScores,
    SearchTrace,
)
from modelryx.engineering_search.normalization import material_search_tokens
from modelryx.engineering_search.ranking import hybrid_score
from modelryx.metadata.array_quality import (
    array_geometry_unverified,
    has_unverified_array_geometry,
)
from modelryx.models import (
    AiDocument,
    ApprovalReviewerGroupMember,
    Artifact,
    ArtifactFile,
    BomLine,
    BomViewRevision,
    Checkout,
    Item,
    MetadataExtraction,
    Revision,
    RevisionApprovalRequest,
    RevisionArtifactRelation,
    RevisionContentSnapshot,
    RevisionContentSnapshotArtifact,
    User,
)


def _numeric_conditions(column, predicate: NumericPredicate):
    conditions = []
    operators = {
        "eq": lambda value: column == value,
        "lt": lambda value: column < value,
        "lte": lambda value: column <= value,
        "gt": lambda value: column > value,
        "gte": lambda value: column >= value,
    }
    for operator, comparison in operators.items():
        value = getattr(predicate, operator)
        if value is not None:
            conditions.append(comparison(value))
    if predicate.target is not None:
        tolerance = (
            predicate.tolerance if predicate.tolerance is not None else predicate.target * 0.1
        )
        conditions.extend(
            [column >= predicate.target - tolerance, column <= predicate.target + tolerance]
        )
    return conditions


def _hole_diameter_conditions(predicate: NumericPredicate):
    """Require one actual hole diameter to satisfy every numeric constraint."""
    holes = func.jsonb_array_elements(
        func.coalesce(
            MetadataExtraction.metadata_json["features"]["holes"],
            literal_column("'[]'::jsonb"),
        )
    ).table_valued("value").alias("search_hole")
    diameter = cast(holes.c.value.op("->>")("diameter_mm"), Float)
    return [
        select(1).select_from(holes).where(*_numeric_conditions(diameter, predicate)).exists()
    ]


def _predicate_label(predicate: NumericPredicate, unit: str) -> str:
    labels = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "target": "~"}
    return " ".join(
        f"{labels[field]} {getattr(predicate, field):g} {unit}"
        for field in labels
        if getattr(predicate, field) is not None
    )


def _actual_material(extraction: MetadataExtraction | None) -> str | None:
    return extraction.material if extraction else None


def _item_text_vector():
    vectors = (
        func.setweight(
            func.to_tsvector("english", func.coalesce(Item.part_number, "")),
            literal_column("'A'"),
        ),
        func.setweight(
            func.to_tsvector("english", func.coalesce(Item.name, "")), literal_column("'A'")
        ),
        func.setweight(
            func.to_tsvector("english", func.coalesce(Item.category, "")),
            literal_column("'B'"),
        ),
        func.setweight(
            func.to_tsvector("english", func.coalesce(Item.description, "")),
            literal_column("'B'"),
        ),
    )
    document = vectors[0]
    for vector in vectors[1:]:
        document = document.op("||")(vector)
    return document


def _has_structured_filters(request: SearchRequest) -> bool:
    filters = request.filters
    return bool(
        filters.identifiers
        or filters.item_number_regex
        or filters.name_regex
        or filters.classification
        or filters.material
        or filters.material_presence
        or filters.feature_filters
        or filters.feature_count_filters
        or filters.geometry
        or filters.mass_kg
        or filters.density_kg_m3
        or filters.hole_diameter_mm
        or filters.drawing_count
        or filters.tolerance_count
        or filters.instance_count
        or filters.cad_system
        or filters.item_types is not None
        or filters.cad_systems is not None
        or filters.item_statuses is not None
        or filters.artifact_format
        or filters.revision
        or filters.status
        or filters.lifecycle
        or filters.owner
        or filters.created
        or filters.modified
        or filters.approval_state
        or filters.approval_assigned_to_me
        or filters.approval_due_before
        or filters.checkout_owner
        or filters.artifact_presence
        or filters.artifact_missing is not None
        or filters.extraction_status
        or filters.has_warnings is not None
    )


def _relationship_matches(relationship):
    latest_sequence = (
        select(func.max(BomViewRevision.sequence))
        .where(BomViewRevision.revision_id == BomLine.parent_revision_id)
        .correlate(BomLine)
        .scalar_subquery()
    )
    seed = (
        select(
            BomLine.parent_revision_id.label("revision_id"),
            literal(1).label("depth"),
        )
        .join(BomViewRevision, BomViewRevision.id == BomLine.bom_view_revision_id)
        .join(Revision, Revision.id == BomLine.child_revision_id)
        .where(
            BomViewRevision.sequence == latest_sequence,
            Revision.part_id == relationship.target_item_id,
        )
    )
    traversal = seed.cte(recursive=True)
    if relationship.depth == "recursive":
        parent = aliased(BomLine)
        parent_view = aliased(BomViewRevision)
        parent_latest_sequence = (
            select(func.max(BomViewRevision.sequence))
            .where(BomViewRevision.revision_id == parent.parent_revision_id)
            .correlate(parent)
            .scalar_subquery()
        )
        traversal = traversal.union_all(
            select(parent.parent_revision_id, (traversal.c.depth + 1).label("depth"))
            .join(parent_view, parent_view.id == parent.bom_view_revision_id)
            .where(
                parent.child_revision_id == traversal.c.revision_id,
                parent_view.sequence == parent_latest_sequence,
                traversal.c.depth < relationship.max_depth,
            )
        )
    return (
        select(
            traversal.c.revision_id,
            func.min(traversal.c.depth).label("depth"),
        )
        .group_by(traversal.c.revision_id)
        .subquery()
    )


def execute_search(
    db: Session,
    principal: Principal,
    request: SearchRequest,
    *,
    parser: str = "direct",
    warnings: list[str] | None = None,
    confidence: float | None = None,
) -> EngineeringSearchResponse:
    started = time.monotonic()
    filters = request.filters
    # AUTO identities are unresolved assembly components, not Library entries.
    # They remain available through their assembly structure and direct Item APIs.
    conditions = [Item.status == "ACTIVE", ~Item.part_number.startswith("AUTO-")]
    current_user = None
    if principal.role == "administrator":
        conditions.append(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
    else:
        current_user = user_for_principal(db, principal)
        conditions.append(
            or_(
                Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
                Item.created_by == current_user.id,
            )
        )
    if request.entity_type == "part":
        conditions.append(Item.part_type == "PART")
    elif request.entity_type == "assembly":
        conditions.append(Item.part_type == "ASSEMBLY")
    coverage_scope_conditions = list(conditions)
    unknown_coverage_conditions = []
    coverage_rules: set[str] = set()

    text_score = cast(0, Float)
    filename_match = cast(None, String)
    if request.text.terms:
        query = " ".join(request.text.terms)
        text_query = func.plainto_tsquery("english", query)
        item_document = _item_text_vector()
        revision_document = func.to_tsvector("english", func.coalesce(Revision.search_text, ""))
        extraction_document = func.to_tsvector(
            "english",
            func.concat_ws(
                " ",
                cast(MetadataExtraction.metadata_json, String),
                cast(MetadataExtraction.derived_json, String),
            ),
        )
        artifact_document = func.to_tsvector(
            "english", func.coalesce(ArtifactFile.original_filename, "")
        )
        matching_artifact = (
            select(ArtifactFile.original_filename)
            .join(Artifact, Artifact.id == ArtifactFile.artifact_id)
            .join(
                RevisionArtifactRelation,
                RevisionArtifactRelation.artifact_id == Artifact.id,
            )
            .join(
                RevisionContentSnapshotArtifact,
                RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
            )
            .where(
                RevisionArtifactRelation.revision_id == Revision.id,
                RevisionContentSnapshotArtifact.snapshot_id
                == select(RevisionContentSnapshot.id)
                .where(RevisionContentSnapshot.revision_id == Revision.id)
                .order_by(RevisionContentSnapshot.sequence.desc())
                .limit(1)
                .correlate(Revision)
                .scalar_subquery(),
                ArtifactFile.state.in_(["READY", "VERIFIED"]),
                or_(
                    artifact_document.op("@@")(text_query),
                    and_(
                        *(
                            func.lower(ArtifactFile.original_filename).contains(term.casefold())
                            for term in request.text.terms
                        )
                    ),
                ),
            )
            .order_by(ArtifactFile.original_filename)
            .limit(1)
            .correlate(Revision)
        )
        filename_match = matching_artifact.scalar_subquery()
        raw_rank = (
            func.ts_rank_cd(item_document, text_query)
            + 0.6 * func.ts_rank_cd(revision_document, text_query)
            + 0.4 * func.ts_rank_cd(extraction_document, text_query)
            + case((filename_match.is_not(None), 0.2), else_=0.0)
            + case((func.lower(Item.part_number) == query.casefold(), 1.0), else_=0.0)
            + case((func.lower(Item.name) == query.casefold(), 0.5), else_=0.0)
        )
        text_score = func.least(1.0, 1.0 - func.exp(-3.0 * raw_rank))
        conditions.append(
            or_(
                item_document.op("@@")(text_query),
                revision_document.op("@@")(text_query),
                extraction_document.op("@@")(text_query),
                filename_match.is_not(None),
            )
        )
    if filters.identifiers:
        normalized_ids = [value.casefold() for value in filters.identifiers]
        conditions.append(func.lower(Item.part_number).in_(normalized_ids))
    if filters.item_number_regex:
        conditions.append(Item.part_number.op("~*")(filters.item_number_regex))
    if filters.name_regex:
        conditions.append(Item.name.op("~*")(filters.name_regex))
    if filters.classification:
        conditions.append(func.lower(Item.category) == filters.classification.casefold())
    if filters.cad_system:
        # Treat the requested value as a CAD product family. For example, the
        # AutoCAD filter must include revisions recorded as "AutoCAD Mechanical".
        conditions.append(func.lower(Revision.cad_system).contains(filters.cad_system.casefold()))
    if filters.artifact_format:
        format_extensions = {
            "step": (".step", ".stp"),
            "iges": (".iges", ".igs"),
        }
        extensions = format_extensions.get(
            filters.artifact_format.casefold().lstrip("."),
            ("." + filters.artifact_format.casefold().lstrip("."),),
        )
        latest_snapshot_id = (
            select(RevisionContentSnapshot.id)
            .where(RevisionContentSnapshot.revision_id == Revision.id)
            .order_by(RevisionContentSnapshot.sequence.desc())
            .limit(1)
            .correlate(Revision)
            .scalar_subquery()
        )
        matching_format = (
            select(ArtifactFile.id)
            .join(Artifact, Artifact.id == ArtifactFile.artifact_id)
            .join(RevisionArtifactRelation, RevisionArtifactRelation.artifact_id == Artifact.id)
            .join(
                RevisionContentSnapshotArtifact,
                RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
            )
            .where(
                RevisionArtifactRelation.revision_id == Revision.id,
                RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot_id,
                ArtifactFile.state.in_(["READY", "VERIFIED"]),
                or_(
                    *(
                        func.lower(ArtifactFile.original_filename).endswith(extension)
                        for extension in extensions
                    )
                ),
            )
            .correlate(Revision)
            .exists()
        )
        conditions.append(matching_format)
    if filters.artifact_presence:
        relation_types = {
            "drawing": ("DRAWING",),
            "preview": ("REPRESENTATION", "THUMBNAIL"),
            "native_cad": ("SPECIFICATION",),
        }[filters.artifact_presence]
        latest_snapshot_id = (
            select(RevisionContentSnapshot.id)
            .where(RevisionContentSnapshot.revision_id == Revision.id)
            .order_by(RevisionContentSnapshot.sequence.desc())
            .limit(1)
            .correlate(Revision)
            .scalar_subquery()
        )
        artifact_exists = (
            select(RevisionArtifactRelation.id)
            .join(
                RevisionContentSnapshotArtifact,
                RevisionContentSnapshotArtifact.relation_id == RevisionArtifactRelation.id,
            )
            .where(
                RevisionArtifactRelation.revision_id == Revision.id,
                RevisionArtifactRelation.relation_type.in_(relation_types),
                RevisionContentSnapshotArtifact.snapshot_id == latest_snapshot_id,
            )
            .correlate(Revision)
            .exists()
        )
        conditions.append(~artifact_exists if filters.artifact_missing else artifact_exists)
        coverage_rules.add("snapshot_relation")
    if filters.revision:
        conditions.append(func.lower(Revision.revision_code) == filters.revision.casefold())
    if filters.status:
        conditions.append(Revision.state == filters.status.upper())
    if filters.lifecycle:
        conditions.append(Revision.lifecycle_state == filters.lifecycle)
    if filters.owner:
        if filters.owner == "$me":
            if current_user is None:
                current_user = user_for_principal(db, principal)
            conditions.append(Item.created_by == current_user.id)
        else:
            owner = filters.owner.casefold()
            conditions.append(
                or_(
                    func.lower(User.external_subject) == owner,
                    func.lower(User.display_name) == owner,
                )
            )
    if filters.created:
        if filters.created.after:
            conditions.append(Item.created_at >= filters.created.after)
        if filters.created.before:
            conditions.append(Item.created_at <= filters.created.before)
    if filters.modified:
        if filters.modified.after:
            conditions.append(Item.updated_at >= filters.modified.after)
        if filters.modified.before:
            conditions.append(Item.updated_at <= filters.modified.before)

    if filters.approval_state or filters.approval_assigned_to_me or filters.approval_due_before:
        approval_conditions = [RevisionApprovalRequest.revision_id == Revision.id]
        if filters.approval_state:
            approval_conditions.append(RevisionApprovalRequest.status == filters.approval_state)
        if filters.approval_due_before:
            approval_conditions.append(RevisionApprovalRequest.due_at < filters.approval_due_before)
        if filters.approval_assigned_to_me:
            if current_user is None:
                current_user = user_for_principal(db, principal)
            approval_conditions.extend(
                [
                    RevisionApprovalRequest.requested_by != current_user.id,
                    RevisionApprovalRequest.reviewer_group_id.in_(
                        select(ApprovalReviewerGroupMember.group_id).where(
                            ApprovalReviewerGroupMember.user_id == current_user.id
                        )
                    ),
                ]
            )
        conditions.append(select(RevisionApprovalRequest.id).where(*approval_conditions).exists())
        coverage_rules.add("snapshot_relation")
    if filters.checkout_owner:
        if filters.checkout_owner == "$me":
            if current_user is None:
                current_user = user_for_principal(db, principal)
            checkout_owner_condition = Checkout.owner_id == current_user.id
        else:
            checkout_user = aliased(User)
            checkout_owner = filters.checkout_owner.casefold()
            checkout_owner_condition = Checkout.owner_id.in_(
                select(checkout_user.id).where(
                    or_(
                        func.lower(checkout_user.external_subject) == checkout_owner,
                        func.lower(checkout_user.display_name) == checkout_owner,
                    )
                )
            )
        conditions.append(
            select(Checkout.id)
            .where(
                Checkout.revision_id == Revision.id,
                Checkout.expires_at > datetime.now(UTC),
                checkout_owner_condition,
            )
            .exists()
        )
        coverage_rules.add("authoritative_field")
    if filters.extraction_status:
        extraction_conditions = {
            "available": MetadataExtraction.status.in_(["READY", "PARTIAL"]),
            "missing": MetadataExtraction.id.is_(None),
            "failed": MetadataExtraction.status == "INVALID",
        }
        conditions.append(extraction_conditions[filters.extraction_status])
        coverage_rules.add("successful_extraction")
    if filters.has_warnings is not None:
        conditions.append(
            Revision.state == "READY_WITH_WARNINGS"
            if filters.has_warnings
            else Revision.state != "READY_WITH_WARNINGS"
        )
        coverage_rules.add("authoritative_field")

    if filters.material:
        material_column = func.lower(func.coalesce(MetadataExtraction.material, ""))
        tokens = material_search_tokens(filters.material)
        family_tokens = (
            tokens[:2] if filters.material.family in {"aluminum", "stainless_steel"} else tokens[:1]
        )
        conditions.append(or_(*(material_column.contains(token) for token in family_tokens)))
        if filters.material.grade:
            conditions.append(material_column.contains(filters.material.grade.casefold()))
        if filters.material.temper:
            conditions.append(material_column.contains(filters.material.temper.casefold()))
    if filters.material_presence == "missing":
        conditions.append(
            and_(
                MetadataExtraction.id.is_not(None),
                or_(
                MetadataExtraction.material.is_(None),
                func.btrim(MetadataExtraction.material) == "",
                ),
            )
        )
        unknown_coverage_conditions.append(MetadataExtraction.id.is_(None))
        coverage_rules.add("successful_extraction")
    elif filters.material_presence == "specified":
        conditions.append(
            and_(
                MetadataExtraction.material.is_not(None),
                func.btrim(MetadataExtraction.material) != "",
            )
        )
    feature_columns = {
        feature: getattr(MetadataExtraction, f"{feature}_count")
        for feature in (
            "hole",
            "fillet",
            "chamfer",
            "extrusion",
            "revolution",
            "pocket",
            "pattern",
            "thread",
        )
    }
    for feature_filter in filters.feature_filters:
        column = feature_columns[feature_filter.feature]
        # A numeric zero is authoritative only when the extractor explicitly persisted it.
        # NULL means feature coverage is unavailable or unknown and must not satisfy absence.
        conditions.append(column.is_not(None))
        conditions.append(
            column == 0 if feature_filter.presence == "absent" else column > 0
        )
        unknown_coverage_conditions.append(column.is_(None))
        coverage_rules.add("successful_extraction")
    for count_filter in filters.feature_count_filters:
        column = feature_columns[count_filter.feature]
        conditions.append(column.is_not(None))
        conditions.extend(_numeric_conditions(column, count_filter.count))
        unknown_coverage_conditions.append(column.is_(None))
        coverage_rules.add("successful_extraction")

    geometry_columns = {
        "width_mm": MetadataExtraction.bbox_x_mm,
        "height_mm": MetadataExtraction.bbox_z_mm,
        "length_mm": MetadataExtraction.bbox_y_mm,
        "max_dimension_mm": MetadataExtraction.bbox_max_mm,
        "volume_mm3": MetadataExtraction.volume_mm3,
        "surface_area_mm2": MetadataExtraction.surface_area_mm2,
    }
    if filters.geometry:
        for field, column in geometry_columns.items():
            predicate = getattr(filters.geometry, field)
            if predicate:
                conditions.append(~array_geometry_unverified(MetadataExtraction.metadata_json))
                conditions.extend(_numeric_conditions(column, predicate))
                unknown_coverage_conditions.append(
                    or_(
                        column.is_(None),
                        array_geometry_unverified(MetadataExtraction.metadata_json),
                    )
                )
                coverage_rules.add("successful_extraction")
    if filters.mass_kg:
        conditions.append(~array_geometry_unverified(MetadataExtraction.metadata_json))
        conditions.extend(_numeric_conditions(MetadataExtraction.mass_kg, filters.mass_kg))
        unknown_coverage_conditions.append(
            or_(
                MetadataExtraction.mass_kg.is_(None),
                array_geometry_unverified(MetadataExtraction.metadata_json),
            )
        )
        coverage_rules.add("successful_extraction")
    numeric_metadata_filters = {
        "density_kg_m3": MetadataExtraction.density_kg_m3,
        "drawing_count": MetadataExtraction.drawing_count,
        "tolerance_count": MetadataExtraction.tolerance_count,
        "instance_count": MetadataExtraction.instance_count,
    }
    for field, column in numeric_metadata_filters.items():
        predicate = getattr(filters, field)
        if predicate:
            conditions.extend(_numeric_conditions(column, predicate))
            if field == "tolerance_count":
                capabilities = MetadataExtraction.metadata_json["extraction"]["capabilities"]
                incomplete = or_(
                    ~func.coalesce(capabilities["gdt"].as_string(), "").in_(
                        ["complete", "not_applicable"]
                    ),
                    ~func.coalesce(capabilities["drawing_dimensions"].as_string(), "").in_(
                        ["complete", "not_applicable"]
                    ),
                )
                conditions.append(~incomplete)
                unknown_coverage_conditions.append(or_(column.is_(None), incomplete))
            else:
                unknown_coverage_conditions.append(column.is_(None))
            coverage_rules.add("successful_extraction")
    if filters.hole_diameter_mm:
        predicate = filters.hole_diameter_mm
        conditions.extend(_hole_diameter_conditions(predicate))
        unknown_coverage_conditions.append(
            or_(
                MetadataExtraction.hole_diameter_min_mm.is_(None),
                MetadataExtraction.hole_diameter_max_mm.is_(None),
            )
        )
        coverage_rules.add("successful_extraction")

    candidate_document = aliased(AiDocument)
    base = (
        Item.__table__.outerjoin(Revision, Revision.id == Item.current_revision_id)
        .outerjoin(
            MetadataExtraction,
            MetadataExtraction.id == Revision.current_metadata_extraction_id,
        )
        .outerjoin(User, User.id == Item.created_by)
    )
    vector_score = cast(0, Float)
    relationship_score = cast(0, Float)
    relationship_depth = cast(None, Float)
    if request.similarity:
        source_visibility = [
            Revision.id == request.similarity.source_item_revision_id,
            Item.status == "ACTIVE",
        ]
        if principal.role == "administrator":
            source_visibility.append(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
        else:
            source_visibility.append(
                or_(
                    Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
                    Item.created_by == current_user.id,
                )
            )
        source_revision = db.scalar(
            select(Revision).join(Item, Item.id == Revision.part_id).where(*source_visibility)
        )
        if source_revision is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Similarity source revision was not found",
            )
        source_document = db.scalar(
            select(AiDocument).where(
                AiDocument.revision_id == source_revision.id,
                AiDocument.chunk_key == "part-metadata-v1",
                AiDocument.embedding.is_not(None),
            )
        )
        if source_document is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Similarity source revision does not have a metadata embedding",
            )
        base = base.join(
            candidate_document,
            and_(
                candidate_document.revision_id == Revision.id,
                candidate_document.chunk_key == "part-metadata-v1",
                candidate_document.embedding.is_not(None),
            ),
        )
        cosine_distance = candidate_document.embedding.cosine_distance(source_document.embedding)
        vector_score = func.least(1.0, func.greatest(0.0, 1.0 - cosine_distance))
        conditions.extend(
            [
                Revision.id != source_revision.id,
                vector_score >= request.similarity.minimum_score,
            ]
        )
    relationship_matches = []
    for relationship in request.relationships:
        target_visibility = [
            Item.id == relationship.target_item_id,
            Item.status == "ACTIVE",
        ]
        if principal.role == "administrator":
            target_visibility.append(Revision.state.in_(["READY", "READY_WITH_WARNINGS"]))
        else:
            target_visibility.append(
                or_(
                    Revision.state.in_(["READY", "READY_WITH_WARNINGS"]),
                    Item.created_by == current_user.id,
                )
            )
        target_exists = db.scalar(
            select(Item.id)
            .join(Revision, Revision.id == Item.current_revision_id)
            .where(*target_visibility)
        )
        if target_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Relationship target item was not found",
            )
        matches = _relationship_matches(relationship)
        relationship_matches.append(matches)
        conditions.append(Revision.id.in_(select(matches.c.revision_id)))
    if relationship_matches:
        primary_matches = relationship_matches[0]
        relationship_depth = (
            select(primary_matches.c.depth)
            .where(primary_matches.c.revision_id == Revision.id)
            .scalar_subquery()
        )
        relationship_score = 1.0 / relationship_depth
    # Facets are evaluated under the same visibility and search conditions as
    # results. Each omits only its own multi-select so choices remain available.
    facet_columns = {
        "item_types": Item.part_type,
        "cad_systems": Revision.cad_system,
        "item_statuses": Item.status,
    }
    facet_predicates = {}
    for key, column in facet_columns.items():
        values = getattr(filters, key)
        if values is not None:
            non_null = [value for value in values if value is not None]
            choices = [column.in_(non_null)]
            if None in values:
                choices.append(or_(column.is_(None), column == ""))
            facet_predicates[key] = or_(*choices)
    facets = None
    if request.include_facets:
        facets = {}
        for key, column in facet_columns.items():
            scope = [
                *conditions, *(value for name, value in facet_predicates.items() if name != key)
            ]
            query = select(column).select_from(base).where(and_(*scope)).distinct()
            values = db.scalars(query).all()
            facets[key] = sorted(
                {value or None for value in values}, key=lambda value: (value is None, value or "")
            )
    predicate = and_(*conditions, *facet_predicates.values())
    metadata_score = literal(1.0 if _has_structured_filters(request) else 0.0, Float)
    active_scores = {}
    if request.text.terms:
        active_scores["text"] = text_score
    if request.similarity:
        active_scores["vector"] = vector_score
    if _has_structured_filters(request):
        active_scores["metadata"] = metadata_score
    if request.relationships:
        active_scores["relationship"] = relationship_score
    final_score = hybrid_score(active_scores) if active_scores else literal(0.0, Float)
    total = db.scalar(select(func.count()).select_from(base).where(predicate)) or 0
    excluded_unknown = 0
    if unknown_coverage_conditions:
        excluded_unknown = db.scalar(
            select(func.count()).select_from(base).where(
                and_(*coverage_scope_conditions), or_(*unknown_coverage_conditions)
            )
        ) or 0
    statement = (
        select(
            Item,
            Revision,
            MetadataExtraction,
            text_score.label("text_score"),
            metadata_score.label("metadata_score"),
            vector_score.label("vector_score"),
            relationship_score.label("relationship_score"),
            relationship_depth.label("relationship_depth"),
            final_score.label("final_score"),
            filename_match.label("filename_match"),
        )
        .select_from(base)
        .where(predicate)
    )
    sort = request.sort[0]
    sort_columns = {
        "item_number": Item.part_number,
        "name": Item.name,
        "created": Item.created_at,
        "modified": Item.updated_at,
        "relevance": final_score,
        "item_type": Item.part_type,
        "revision": Revision.revision_code,
        "cad_system": Revision.cad_system,
        "item_status": Item.status,
    }
    sort_column = sort_columns[sort.field]
    statement = (
        statement.order_by(
            sort_column.desc() if sort.direction == "desc" else sort_column.asc(),
            Item.part_number.asc(),
            Item.id.asc(),
        )
        .limit(request.limit)
        .offset(request.offset)
    )

    results: list[EngineeringSearchItem] = []
    output_warnings = list(warnings or [])
    for (
        item,
        revision,
        extraction,
        text,
        metadata,
        vector,
        relationship_value,
        matched_depth,
        final,
        matched_filename,
    ) in db.execute(statement).all():
        uncertain_geometry = bool(
            extraction and has_unverified_array_geometry(extraction.metadata_json)
        )
        if uncertain_geometry:
            warning = (
                "Unverified array geometry measurements were omitted from one or more results."
            )
            if warning not in output_warnings:
                output_warnings.append(warning)
        reasons: list[MatchReason] = []
        if filters.item_number_regex:
            reasons.append(
                MatchReason(
                    type="item_number_regex",
                    query=filters.item_number_regex,
                    actual=item.part_number,
                )
            )
        if filters.name_regex:
            reasons.append(
                MatchReason(type="name_regex", query=filters.name_regex, actual=item.name)
            )
        if request.text.terms:
            term_values = [term.casefold() for term in request.text.terms]
            searchable_fields = {
                "item number": item.part_number,
                "name": item.name,
                "description": item.description or "",
                "classification": item.category or "",
                "revision metadata": revision.search_text if revision else "",
                "normalized metadata": str(extraction.metadata_json) if extraction else "",
            }
            matched_fields = [
                name
                for name, value in searchable_fields.items()
                if any(term in value.casefold() for term in term_values)
            ]
            if matched_filename:
                matched_fields.append("filename")
            reasons.append(
                MatchReason(
                    type="text",
                    query=" ".join(request.text.terms),
                    actual=", ".join(matched_fields) or "full-text stem match",
                )
            )
            if matched_filename:
                reasons.append(MatchReason(type="filename", query=query, actual=matched_filename))
        if filters.material:
            reasons.append(
                MatchReason(
                    type="material",
                    query=" ".join(
                        filter(
                            None,
                            [
                                filters.material.family,
                                filters.material.grade,
                                filters.material.temper,
                            ],
                        )
                    ),
                    actual=_actual_material(extraction),
                )
            )
        if filters.material_presence:
            reasons.append(
                MatchReason(
                    type="material_presence",
                    query=filters.material_presence,
                    actual=_actual_material(extraction),
                )
            )
        for feature_filter in filters.feature_filters:
            count = (
                getattr(extraction, f"{feature_filter.feature}_count")
                if extraction
                else None
            )
            reasons.append(
                MatchReason(
                    type="feature_presence",
                    query=f"{feature_filter.feature}:{feature_filter.presence}",
                    actual=float(count) if count is not None else None,
                )
            )
        for count_filter in filters.feature_count_filters:
            count = (
                getattr(extraction, f"{count_filter.feature}_count")
                if extraction
                else None
            )
            reasons.append(
                MatchReason(
                    type="feature_count",
                    query=f"{count_filter.feature} {_predicate_label(count_filter.count, 'count')}",
                    actual=float(count) if count is not None else None,
                )
            )
        if filters.geometry:
            for field in geometry_columns:
                numeric = getattr(filters.geometry, field)
                if numeric:
                    actual = {
                        "width_mm": extraction.bbox_x_mm,
                        "height_mm": extraction.bbox_z_mm,
                        "length_mm": extraction.bbox_y_mm,
                        "max_dimension_mm": extraction.bbox_max_mm,
                        "volume_mm3": extraction.volume_mm3,
                        "surface_area_mm2": extraction.surface_area_mm2,
                    }[field]
                    unit = (
                        "mm3"
                        if field == "volume_mm3"
                        else "mm2"
                        if field == "surface_area_mm2"
                        else "mm"
                    )
                    reasons.append(
                        MatchReason(
                            type=field, query=_predicate_label(numeric, unit), actual=actual
                        )
                    )
        if filters.mass_kg:
            reasons.append(
                MatchReason(
                    type="mass_kg",
                    query=_predicate_label(filters.mass_kg, "kg"),
                    actual=extraction.mass_kg,
                )
            )
        if request.similarity:
            reasons.append(
                MatchReason(
                    type="vector_similarity",
                    query=str(request.similarity.source_item_revision_id),
                    actual=float(vector or 0),
                )
            )
        for relationship in request.relationships:
            reasons.append(
                MatchReason(
                    type=relationship.type,
                    query=str(relationship.target_item_id),
                    actual=float(matched_depth or 1),
                )
            )
        text_value = min(1.0, max(0.0, float(text or 0)))
        metadata_value = min(1.0, max(0.0, float(metadata or 0)))
        vector_value = min(1.0, max(0.0, float(vector or 0)))
        relationship_score_value = min(1.0, max(0.0, float(relationship_value or 0)))
        final_value = min(1.0, max(0.0, float(final or 0)))
        results.append(
            EngineeringSearchItem(
                item_id=item.id,
                revision_id=revision.id if revision else None,
                item_number=item.part_number,
                name=item.name,
                item_type=item.part_type,
                revision_code=revision.revision_code if revision else None,
                cad_system=(
                    revision.cad_system
                    if revision and revision.cad_system
                    else extraction.source_system
                    if extraction
                    else None
                ),
                material=_actual_material(extraction),
                dimensions_mm=(
                    [extraction.bbox_x_mm, extraction.bbox_y_mm, extraction.bbox_z_mm]
                    if extraction and not uncertain_geometry
                    else None
                ),
                mass_kg=extraction.mass_kg if extraction and not uncertain_geometry else None,
                description=item.description,
                category=item.category,
                item_status=item.status,
                metadata_status=extraction.status if extraction else None,
                volume_mm3=extraction.volume_mm3 if extraction and not uncertain_geometry else None,
                surface_area_mm2=(
                    extraction.surface_area_mm2 if extraction and not uncertain_geometry else None
                ),
                solid_count=(
                    extraction.solid_count if extraction and not uncertain_geometry else None
                ),
                face_count=extraction.face_count if extraction and not uncertain_geometry else None,
                edge_count=extraction.edge_count if extraction and not uncertain_geometry else None,
                hole_count=extraction.hole_count if extraction else None,
                fillet_count=extraction.fillet_count if extraction else None,
                chamfer_count=extraction.chamfer_count if extraction else None,
                extrusion_count=extraction.extrusion_count if extraction else None,
                revolution_count=extraction.revolution_count if extraction else None,
                pocket_count=extraction.pocket_count if extraction else None,
                pattern_count=extraction.pattern_count if extraction else None,
                thread_count=extraction.thread_count if extraction else None,
                score=final_value,
                scores=SearchScores(
                    text=text_value,
                    metadata=metadata_value,
                    vector=vector_value,
                    relationship=relationship_score_value,
                    final=final_value,
                ),
                match_reasons=reasons,
            )
        )
    catalog = active_scenario_catalog()
    return EngineeringSearchResponse(
        interpretation=request,
        results=results,
        total=total,
        warnings=output_warnings,
        facets=facets,
        confidence=confidence,
        coverage=SearchCoverage(
            matched=total,
            excluded_unknown=excluded_unknown,
            coverage_rules=sorted(coverage_rules),
        ),
        trace=SearchTrace(
            structured_matches=total,
            returned=len(results),
            parser=parser,
            duration_ms=(time.monotonic() - started) * 1000,
            catalog_version=catalog.version,
            catalog_digest=catalog.digest,
        ),
    )
