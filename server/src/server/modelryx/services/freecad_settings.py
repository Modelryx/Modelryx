"""Deprecated import aliases for CAD-neutral endpoint configuration."""

from modelryx.services.cad_worker_settings import (
    CAD_ANALYZER_KEY as FREECAD_ANALYZER_KEY,
)
from modelryx.services.cad_worker_settings import (
    CAD_WORKER_KEY as FREECAD_WORKER_KEY,
)
from modelryx.services.cad_worker_settings import (
    configured_url as configured_url,
)

__all__ = ["FREECAD_ANALYZER_KEY", "FREECAD_WORKER_KEY", "configured_url"]
