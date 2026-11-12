"""Resolve administrator overrides for standalone FreeCAD endpoints."""

from sqlalchemy.orm import Session

from modelryx.models import ApplicationSetting

FREECAD_WORKER_KEY = "cad.freecad.server_url"
FREECAD_ANALYZER_KEY = "cad.freecad.analyzer_url"


def configured_url(db: Session, key: str, default: str | None) -> str | None:
    setting = db.get(ApplicationSetting, key)
    return (setting.value or None) if setting is not None else default
