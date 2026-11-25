"""CAD-neutral endpoint configuration, with legacy configuration aliases."""

from sqlalchemy.orm import Session

from modelryx.models import ApplicationSetting

CAD_WORKER_KEY = "cad.server_url"
CAD_ANALYZER_KEY = "cad.analyzer_url"
LEGACY_SETTING_KEYS = {
    CAD_WORKER_KEY: "cad.freecad.server_url",
    CAD_ANALYZER_KEY: "cad.freecad.analyzer_url",
}


def configured_url(db: Session, key: str, default: str | None) -> str | None:
    setting = db.get(ApplicationSetting, key)
    if setting is None and key in LEGACY_SETTING_KEYS:
        setting = db.get(ApplicationSetting, LEGACY_SETTING_KEYS[key])
    return (setting.value or None) if setting is not None else default


def worker_default(settings) -> str | None:
    value = getattr(settings, "cad_worker_url", None)
    return (value or None) if value is not None else getattr(settings, "freecad_worker_url", None)


def analyzer_default(settings) -> str | None:
    value = getattr(settings, "cad_preprocessor_url", None)
    if value is not None:
        return value or None
    return (getattr(settings, "freecad_preprocessor_url", None)
            or getattr(settings, "cad_analyzer_url", None))
