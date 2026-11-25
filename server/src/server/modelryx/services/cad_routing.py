"""Worker-independent native routing, identity normalization and job control."""

import re
from pathlib import Path, PurePosixPath

from modelryx.services.external_workers import JOB_TYPE, matching_worker

# Old persisted job names remain readable; all native jobs use the same dispatcher.
LEGACY_NATIVE_JOB_TYPES = frozenset({"FCSTD_TO_MODELRYX"})
NATIVE_JOB_TYPES = LEGACY_NATIVE_JOB_TYPES | {JOB_TYPE}
NEUTRAL_JOB_TYPES = frozenset({"STEP_TO_MODELRYX"})
CANCELLABLE_JOB_TYPES = NATIVE_JOB_TYPES | NEUTRAL_JOB_TYPES | {"CAD_SESSION_EXECUTE"}
PLATFORM_WORKER_TYPES = frozenset({"STEP", "GENERAL"})


def can_cancel_processing(job):
    return job.state == "PENDING" or (
        job.state == "RUNNING"
        and job.type in CANCELLABLE_JOB_TYPES
        and job.stage not in {"PUBLISHING", "COMPLETED"}
    )


def processing_type_for_source(filename, cad_system, db=None):
    if Path(filename).suffix.casefold() in {".step", ".stp"}:
        return "STEP_TO_MODELRYX"
    if db is not None and matching_worker(db, filename, cad_system):
        return JOB_TYPE
    return None


def identity_token(value, source_extensions=(), *, child=False):
    text = PurePosixPath(str(value or "").replace("\\", "/")).name
    if child:
        text = re.sub(r":\d+$", "", text)
    extensions = sorted(set(source_extensions), key=len, reverse=True)
    if extensions:
        pattern = "(?:" + "|".join(re.escape(value) for value in extensions) + r")(?=:\d+$|$)"
        text = re.sub(pattern, "", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", text.casefold())
