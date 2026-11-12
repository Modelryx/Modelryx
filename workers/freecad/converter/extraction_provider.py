"""Shared result contract for native FreeCAD extraction providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderResult:
    records: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    evidence: dict[str, Any]
    capability: str
    duration_ms: float
