import hashlib
from datetime import UTC, datetime
from pathlib import Path

from modelryx.models import CADSession

ACTIVE_SESSION_STATES = {
    "CREATED",
    "OPENING",
    "READY",
    "EXECUTING",
    "VERIFYING",
    "READY_TO_REVIEW",
}
TERMINAL_SESSION_STATES = {"COMMITTED", "FAILED", "DISCARDED", "CANCELLED", "EXPIRED"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def isolated_workspace_path(storage_root: Path, session_id, filename: str) -> Path:
    safe_suffix = Path(filename).suffix.lower()
    if not safe_suffix or len(safe_suffix) > 20 or not safe_suffix[1:].isalnum():
        raise ValueError("CAD sessions require a filename with a safe native extension")
    root = (storage_root.resolve() / "cad-sessions").resolve()
    target = (root / str(session_id) / f"source{safe_suffix}").resolve()
    if not target.is_relative_to(root):
        raise ValueError("CAD session workspace escapes the configured root")
    return target


def copy_verified_source(source: Path, target: Path, expected_sha256: str, max_bytes: int) -> None:
    if not source.is_file():
        raise FileNotFoundError("Authoritative native artifact is unavailable")
    size = source.stat().st_size
    if size > max_bytes:
        raise ValueError("Native artifact exceeds the CAD session size limit")
    if file_sha256(source) != expected_sha256:
        raise ValueError("Authoritative native artifact checksum does not match its record")
    target.parent.mkdir(parents=True, exist_ok=False)
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            writer.write(chunk)
    if file_sha256(target) != expected_sha256:
        target.unlink(missing_ok=True)
        raise ValueError("CAD session source copy failed checksum verification")


def expire_if_needed(session: CADSession) -> bool:
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if session.status in ACTIVE_SESSION_STATES and expires_at <= datetime.now(UTC):
        session.status = "EXPIRED"
        session.closed_at = datetime.now(UTC)
        session.lock_version += 1
        return True
    return False


def remove_workspace(storage_root: Path, workspace_key: str) -> None:
    root = (storage_root.resolve() / "cad-sessions").resolve()
    target = (storage_root.resolve() / workspace_key).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("Refusing to remove an invalid CAD session workspace")
    if not target.exists():
        return
    for path in sorted(target.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    target.rmdir()
