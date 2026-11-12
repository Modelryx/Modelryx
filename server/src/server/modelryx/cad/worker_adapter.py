from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class CADWorkerAdapter(Protocol):
    """Vendor-owned behavior loaded only by a CAD worker process."""

    worker_id: str
    worker_type: str
    worker_version: str
    generator: str

    def application_version(self) -> str | None: ...

    def compatibility(self) -> tuple[str, str | None]: ...

    def capabilities(self) -> dict[str, Any]: ...

    def convert(
        self,
        source: Path,
        output_package: Path,
        *,
        timeout_seconds: int,
        max_nodes: int,
        max_triangles: int,
        progress_callback: Callable[[str, int, str], None] | None,
        cancellation_callback: Callable[[], None] | None,
    ) -> None: ...

    def execute_plan(
        self,
        plan: Path,
        source: Path,
        output: Path,
        report: Path,
        *,
        timeout_seconds: int,
        cancellation_callback: Callable[[], None] | None = None,
    ) -> None: ...


class MissingCADWorkerAdapter:
    worker_id = "unavailable"
    worker_type = "CAD"
    worker_version = "unavailable"
    generator = "modelryx-cad-worker"

    def application_version(self) -> str | None:
        return None

    def compatibility(self) -> tuple[str, str | None]:
        return "INCOMPATIBLE", "No CAD worker adapter is configured in this process."

    def capabilities(self) -> dict[str, Any]:
        return {}

    def convert(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("CAD conversion is unavailable in this process")

    def execute_plan(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("CAD editing is unavailable in this process")


def load_worker_adapter() -> CADWorkerAdapter:
    module_name = os.environ.get("MODELRYX_CAD_WORKER_ADAPTER", "").strip()
    if not module_name:
        return MissingCADWorkerAdapter()
    module = importlib.import_module(module_name)
    factory = getattr(module, "create_adapter", None)
    if not callable(factory):
        raise RuntimeError(f"CAD worker adapter {module_name!r} has no create_adapter()")
    return factory()
