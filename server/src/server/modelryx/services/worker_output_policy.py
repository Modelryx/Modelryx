"""CAD-neutral, bounded publication policy declared by external workers."""

BUNDLE_CONTRACTS = {
    "component-geometry": "component-geometry-step-v1",
    "drawing-representations": "related-drawing-v1",
    "manufacturing-outputs": "manufacturing-outputs-v1",
}
BASE_OUTPUTS = {
    "native-snapshot": ("application/json", "metadata"),
    "part-metadata": ("application/json", "metadata"),
    "model-preview": ("application/json", "metadata"),
    "glb": ("model/gltf-binary", "glb"),
    "drawing-pdf": ("application/pdf", "pdf"),
    "step": ("model/step", "package"),
}


def validate_processing_profile(value, document_kinds, output_roles):
    if not isinstance(value, dict):
        raise ValueError("Worker processingProfile must be an object")
    preferred = value.get("preferredOutputs")
    if not isinstance(preferred, dict) or set(preferred) != set(document_kinds):
        raise ValueError("Worker preferredOutputs must cover its declared document kinds")
    normalized = {}
    for kind, roles in preferred.items():
        if (
            not isinstance(roles, list)
            or len(roles) > 32
            or any(
                not isinstance(role, str)
                or role not in output_roles
                or role in {"native-snapshot", "part-metadata", "model-preview"}
                for role in roles
            )
            or len(set(roles)) != len(roles)
        ):
            raise ValueError("Worker preferredOutputs contains invalid or unadvertised roles")
        normalized[kind] = list(roles)
    contracts = value.get("outputContracts", {})
    if not isinstance(contracts, dict) or len(contracts) > len(BUNDLE_CONTRACTS):
        raise ValueError("Worker outputContracts must be a bounded object")
    for role, contract in contracts.items():
        if (
            role not in output_roles
            or role not in BUNDLE_CONTRACTS
            or contract != BUNDLE_CONTRACTS[role]
        ):
            raise ValueError("Worker advertises an unsupported output contract")
    related = value.get("includeRelatedDrawings", False)
    if type(related) is not bool or related and "drawing" not in document_kinds:
        raise ValueError("Worker includeRelatedDrawings requires drawing support and a boolean")
    return {
        "preferredOutputs": normalized,
        "outputContracts": dict(contracts),
        "includeRelatedDrawings": related,
    }


def allowed_worker_outputs(settings, profile=None):
    limits = {
        "metadata": settings.max_preview_bom_bytes,
        "glb": settings.max_preview_glb_bytes,
        "pdf": settings.max_pdf_preview_bytes,
        "package": settings.max_preview_package_bytes,
    }
    outputs = {role: (media, limits[limit]) for role, (media, limit) in BASE_OUTPUTS.items()}
    for role, contract in (profile or {}).get("outputContracts", {}).items():
        if role in BUNDLE_CONTRACTS and BUNDLE_CONTRACTS[role] == contract:
            outputs[role] = ("application/zip", settings.max_preview_package_bytes)
    return outputs


def preferred_worker_outputs(
    document_kind, has_related_drawings, supported_roles=None, profile=None
):
    outputs = ["glb"]
    if document_kind == "drawing":
        outputs.append("drawing-pdf")
    elif has_related_drawings:
        outputs.append("drawing-representations")
    if document_kind == "assembly":
        outputs.append("component-geometry")
    if profile is not None:
        outputs = profile["preferredOutputs"][document_kind]
    publishable = set(BASE_OUTPUTS)
    publishable.update(
        role
        for role, contract in (profile or {}).get("outputContracts", {}).items()
        if role in BUNDLE_CONTRACTS and BUNDLE_CONTRACTS[role] == contract
    )
    return [
        role
        for role in outputs
        if role in publishable and (supported_roles is None or role in supported_roles)
    ]
