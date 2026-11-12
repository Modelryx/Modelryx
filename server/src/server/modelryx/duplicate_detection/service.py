"""Quality-aware deterministic scoring and conservative classification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from modelryx.metadata.array_quality import has_unverified_array_geometry

from .models import Classification, DuplicateResult, ScoreBreakdown


def numeric_similarity(a: float, b: float) -> float:
    return max(0.0, 1.0 - abs(a - b) / max(abs(a), abs(b), 1e-12))


def count_similarity(a: int, b: int) -> float:
    return 1.0 - abs(a - b) / max(a, b, 1)


def _weighted(values: list[tuple[float, float]]) -> float | None:
    total = sum(weight for _, weight in values)
    return sum(score * weight for score, weight in values) / total if total else None


def _quality_factor(metadata: dict[str, Any], section: str) -> float:
    return {"complete": 1.0, "partial": 0.5}.get(metadata.get("quality", {}).get(section), 0.0)


def _name(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _material(metadata: dict[str, Any]) -> str | None:
    material = metadata.get("physical", {}).get("material", {})
    value = material.get("normalized_name") or material.get("name")
    return _name(str(value)) if value else None


@dataclass(frozen=True)
class DuplicateDetectionConfig:
    section_weights: dict[str, float] = field(
        default_factory=lambda: {
            "geometry": 0.50,
            "features": 0.20,
            "physical": 0.15,
            "material": 0.10,
            "identity": 0.05,
        }
    )
    metadata_weight: float = 0.75
    embedding_weight: float = 0.25
    engineering_duplicate_min: float = 0.985
    likely_duplicate_min: float = 0.90
    similar_min: float = 0.72


class DuplicateDetectionService:
    def __init__(self, config: DuplicateDetectionConfig | None = None):
        self.config = config or DuplicateDetectionConfig()

    def compare(
        self,
        source: dict[str, Any],
        candidate: dict[str, Any],
        *,
        embedding_score: float | None = None,
    ) -> DuplicateResult:
        if source.get("identity", {}).get("kind") != candidate.get("identity", {}).get("kind"):
            return DuplicateResult(
                Classification.DIFFERENT,
                ScoreBreakdown(metadata=0.0, combined=0.0),
                ["Part kinds differ."],
            )
        geometry, bbox_diffs = self._geometry(source, candidate)
        features = self._features(source, candidate)
        physical = self._physical(source, candidate)
        material = self._material(source, candidate)
        identity = self._identity(source, candidate)
        assembly = self._assembly(source, candidate)
        sections = {
            "geometry": geometry,
            "features": features,
            "physical": physical,
            "material": material,
            "identity": identity,
        }
        contributions = []
        for section, score in sections.items():
            quality_section = "physical" if section == "material" else section
            factor = min(
                _quality_factor(source, quality_section),
                _quality_factor(candidate, quality_section),
            )
            if section == "identity":
                factor = 1.0
            if score is not None and factor:
                contributions.append((score, self.config.section_weights[section] * factor))
        if assembly is not None:
            # Assemblies emphasize aggregate geometry and structure.
            metadata_score = (
                _weighted([(geometry, 0.55), (assembly, 0.30), (identity or 0.0, 0.15)])
                if geometry is not None
                else None
            )
        else:
            metadata_score = _weighted(contributions)
        combined = metadata_score
        if metadata_score is not None and embedding_score is not None:
            combined = (
                metadata_score * self.config.metadata_weight
                + embedding_score * self.config.embedding_weight
            )
        scores = ScoreBreakdown(
            geometry,
            features,
            physical,
            material,
            identity,
            assembly,
            metadata_score,
            embedding_score,
            combined,
        )
        if combined is None or geometry is None:
            reason = (
                "Array geometry or element activity is unverified; "
                "geometry and mass were not compared."
                if has_unverified_array_geometry(source) or has_unverified_array_geometry(candidate)
                else "Insufficient comparable geometry metadata."
            )
            return DuplicateResult(
                Classification.INSUFFICIENT_DATA,
                scores,
                [reason],
                recommended_action="review",
            )
        formatted_bbox_differences = ", ".join(f"{difference:.1%}" for difference in bbox_diffs)
        reasons = [
            f"Orientation-independent bounding-box differences: {formatted_bbox_differences}.",
            f"Geometry similarity is {geometry:.1%}.",
        ]
        differences: list[str] = []
        if material == 1.0:
            reasons.append("Normalized material matches.")
        elif material == 0.0:
            differences.append("Normalized materials differ.")
        if features is not None:
            (reasons if features >= 0.95 else differences).append(
                f"Feature similarity is {features:.1%}."
            )
        max_bbox_difference = max(bbox_diffs)
        variant_signal = max_bbox_difference >= 0.10 or (features is not None and features < 0.9)
        if combined >= self.config.engineering_duplicate_min and geometry >= 0.98:
            classification, action = Classification.ENGINEERING_DUPLICATE, "review_existing"
        elif combined >= self.config.similar_min and variant_signal:
            classification, action = Classification.VARIANT, "review_variant"
        elif combined >= self.config.likely_duplicate_min and geometry >= 0.90:
            classification, action = Classification.LIKELY_DUPLICATE, "review_existing"
        elif combined >= self.config.similar_min:
            classification, action = Classification.SIMILAR, "show_related"
        else:
            classification, action = Classification.DIFFERENT, "continue_creation"
        return DuplicateResult(classification, scores, reasons, differences, action)

    @staticmethod
    def _geometry(a: dict[str, Any], b: dict[str, Any]) -> tuple[float | None, list[float]]:
        if has_unverified_array_geometry(a) or has_unverified_array_geometry(b):
            return None, []
        try:
            ga, gb = a["geometry"], b["geometry"]
            ba = sorted(float(ga["bounding_box"][k]) for k in ("x_mm", "y_mm", "z_mm"))
            bb = sorted(float(gb["bounding_box"][k]) for k in ("x_mm", "y_mm", "z_mm"))
            bbox_scores = [numeric_similarity(x, y) for x, y in zip(ba, bb, strict=True)]
            values = [(_weighted([(s, 1) for s in bbox_scores]) or 0, 0.35)]
            for key, weight in (("volume_mm3", 0.25), ("surface_area_mm2", 0.15)):
                if key in ga.get("shape", {}) and key in gb.get("shape", {}):
                    values.append((numeric_similarity(ga["shape"][key], gb["shape"][key]), weight))
            for key, weight in (("solid_count", 0.10), ("face_count", 0.08), ("edge_count", 0.07)):
                if key in ga.get("topology", {}) and key in gb.get("topology", {}):
                    values.append(
                        (count_similarity(ga["topology"][key], gb["topology"][key]), weight)
                    )
            return _weighted(values), [1 - score for score in bbox_scores]
        except (KeyError, TypeError, ValueError):
            return None, []

    @staticmethod
    def _features(a: dict[str, Any], b: dict[str, Any]) -> float | None:
        sa, sb = a.get("features", {}).get("summary", {}), b.get("features", {}).get("summary", {})
        weights = {
            "hole_count": 3,
            "thread_count": 3,
            "pocket_count": 2,
            "pattern_count": 2,
            "chamfer_count": 1.5,
            "fillet_count": 1,
            "extrusion_count": 1,
        }
        return _weighted(
            [(count_similarity(sa[k], sb[k]), w) for k, w in weights.items() if k in sa and k in sb]
        )

    @staticmethod
    def _physical(a: dict[str, Any], b: dict[str, Any]) -> float | None:
        if has_unverified_array_geometry(a) or has_unverified_array_geometry(b):
            return None
        pa, pb = a.get("physical", {}), b.get("physical", {})
        return (
            numeric_similarity(pa["mass_kg"], pb["mass_kg"])
            if "mass_kg" in pa and "mass_kg" in pb
            else None
        )

    @staticmethod
    def _material(a: dict[str, Any], b: dict[str, Any]) -> float | None:
        ma, mb = _material(a), _material(b)
        return (1.0 if ma == mb else 0.0) if ma and mb else None

    @staticmethod
    def _identity(a: dict[str, Any], b: dict[str, Any]) -> float:
        return SequenceMatcher(
            None,
            _name(a.get("identity", {}).get("name", "")),
            _name(b.get("identity", {}).get("name", "")),
        ).ratio()

    @staticmethod
    def _assembly(a: dict[str, Any], b: dict[str, Any]) -> float | None:
        aa, ab = a.get("assembly", {}), b.get("assembly", {})
        if not aa.get("is_assembly") or not ab.get("is_assembly"):
            return None
        keys = ("instance_count", "unique_component_count", "subassembly_count")
        return _weighted([(count_similarity(aa[k], ab[k]), 1) for k in keys if k in aa and k in ab])
