from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RANKING_POLICY_VERSION = "phase2-hybrid-v1"
RANKING_WEIGHTS = {
    "text": 0.30,
    "vector": 0.30,
    "metadata": 0.25,
    "relationship": 0.15,
}


def hybrid_score(active_scores: Mapping[str, Any]):
    """Blend scores after normalizing weights over the active retrieval branches."""
    unknown = set(active_scores) - set(RANKING_WEIGHTS)
    if unknown:
        raise ValueError(f"unknown ranking components: {sorted(unknown)}")
    denominator = sum(RANKING_WEIGHTS[name] for name in active_scores)
    if not denominator:
        return 0.0
    return sum(active_scores[name] * RANKING_WEIGHTS[name] for name in active_scores) / denominator
