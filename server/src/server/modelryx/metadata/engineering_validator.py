"""Cross-field engineering validation for structurally valid PartMetadata."""

from __future__ import annotations

from typing import Any


class EngineeringMetadataError(ValueError):
    """Raised when metadata values are individually valid but mutually inconsistent."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_engineering_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate deterministic relationships and return the unchanged document."""
    errors: list[str] = []
    geometry = metadata["geometry"]
    bbox = geometry["bounding_box"]
    shape = geometry["shape"]
    topology = geometry.get("topology", {})
    identity = metadata["identity"]
    assembly = metadata.get("assembly")

    dimensions = (bbox["x_mm"], bbox["y_mm"], bbox["z_mm"])
    volume = shape["volume_mm3"]
    solid_count = topology.get("solid_count")
    if solid_count == 0 and volume > 0:
        errors.append("geometry has positive volume but solid_count is zero")
    if solid_count and any(dimension == 0 for dimension in dimensions):
        errors.append("solid geometry must have three positive bounding-box dimensions")

    bounding_volume = dimensions[0] * dimensions[1] * dimensions[2]
    tolerance = max(1e-6, bounding_volume * 1e-9)
    # A single solid cannot exceed its own bounding-box volume. Multi-solid parts
    # report the sum of body volumes rather than a boolean union, so overlapping
    # bodies may legitimately exceed the outer bounding box just like assemblies.
    if identity["kind"] == "part" and solid_count == 1 and volume > bounding_volume + tolerance:
        errors.append("part volume exceeds its bounding-box volume")

    if assembly is not None:
        is_assembly = assembly["is_assembly"]
        instance_count = assembly.get("instance_count", 0)
        unique_count = assembly.get("unique_component_count")
        if not is_assembly and instance_count > 0:
            errors.append("non-assembly metadata cannot report component instances")
        if is_assembly and identity["kind"] == "part":
            errors.append("identity.kind part conflicts with assembly.is_assembly true")
        if not is_assembly and identity["kind"] == "assembly":
            errors.append("identity.kind assembly conflicts with assembly.is_assembly false")
        if unique_count is not None and unique_count > instance_count:
            errors.append("unique_component_count cannot exceed instance_count")

    if errors:
        raise EngineeringMetadataError(errors)
    return metadata
