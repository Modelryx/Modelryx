"""Validate and materialize the standalone FreeCAD conversion inputs."""

import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import BinaryIO
from uuid import UUID
from zipfile import ZipFile

from modelryx_worker_sdk import CONTRACT_VERSION
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SOURCE_BYTES = 2 * 1024**3
MAX_MEMBERS = 4096
DOCUMENT_KINDS = ("part", "assembly", "drawing")
OUTPUTS = {
    "modelryx-preview": ("preview.pfg", "application/zip", 1024**3),
    "model-preview": ("model-preview.json", "application/json", 64 * 1024**2),
    "glb": ("model.glb", "model/gltf-binary", 512 * 1024**2),
    "drawing-pdf": ("drawing.pdf", "application/pdf", 50 * 1024**2),
    "part-metadata": ("preview.part-metadata.json", "application/json", 64 * 1024**2),
    "native-snapshot": ("preview.cad-raw-metadata.json", "application/json", 64 * 1024**2),
}
PACKAGE_TYPES = {"application/zip", "application/octet-stream"}


def logical_parts(path: str) -> list[str]:
    parts = path.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or PureWindowsPath(part).is_reserved()
        or re.search(r'[\\:<>"|?*\x00-\x1f]', part)
        for part in parts
    ):
        raise ValueError("Unsafe package path")
    return parts


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshotId: UUID
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size: int = Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)
    rootPath: str

    @field_validator("rootPath")
    @classmethod
    def validate_root(cls, value: str) -> str:
        logical_parts(value)
        if not value.lower().endswith(".fcstd"):
            raise ValueError("The root must be an FCStd document")
        return value


class ConversionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contractVersion: str
    jobId: str
    attempt: int = Field(strict=True, gt=0)
    cadSystem: str
    documentKind: str
    source: Source
    configuration: None = None
    requiredOutputs: list[str] = Field(min_length=1)
    preferredOutputs: list[str] = Field(default_factory=list)
    expiresAt: datetime

    def check_profile(self) -> None:
        if str(UUID(self.jobId)) != self.jobId:
            raise ValueError("jobId must be a canonical UUID")
        if self.contractVersion != CONTRACT_VERSION or self.cadSystem != "freecad":
            raise ValueError("Unsupported worker contract or CAD system")
        if self.documentKind not in DOCUMENT_KINDS:
            raise ValueError("Unsupported document kind")
        if not set(self.requiredOutputs).issubset(OUTPUTS):
            raise ValueError("Unsupported required output role")
        if self.expiresAt.tzinfo is None:
            raise ValueError("expiresAt must include a timezone")


def copy_source(stream: BinaryIO, output: Path, check_interrupted: Callable[[], None]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as target:
        while chunk := stream.read(1024 * 1024):
            check_interrupted()
            target.write(chunk)


def materialize_source(
    source: Path,
    destination: Path,
    root_path: str,
    media_type: str,
    check_interrupted: Callable[[], None],
) -> Path:
    """Reconstruct an isolated FCStd workspace with case-preserving dependency paths."""
    root = destination.joinpath(*logical_parts(root_path))
    if media_type == "application/octet-stream":
        with source.open("rb") as stream:
            copy_source(stream, root, check_interrupted)
        return root

    with ZipFile(source) as archive:
        entries = archive.infolist()
        if (
            len(entries) > MAX_MEMBERS
            or sum(entry.file_size for entry in entries) > MAX_SOURCE_BYTES
        ):
            raise ValueError("Dependency package exceeds its expansion limits")
        seen: dict[str, str] = {}
        members = []
        for entry in entries:
            name = entry.orig_filename.removesuffix("/") if entry.is_dir() else entry.orig_filename
            parts = logical_parts(name)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in {0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG}:
                raise ValueError(
                    "Dependency packages may contain only regular files and directories"
                )
            for index in range(1, len(parts) + 1):
                prefix = "/".join(parts[:index])
                if seen.setdefault(prefix.casefold(), prefix) != prefix:
                    raise ValueError("Dependency package has ambiguous paths")
            if entry.is_dir():
                continue
            if not (name.lower().endswith(".fcstd") or name == "_modelryx/dependency-report.json"):
                raise ValueError("Unsupported file in the FreeCAD dependency package")
            members.append((entry, parts))
        files = {entry.filename for entry, _ in members}
        if len(files) != len(members) or root_path not in files:
            raise ValueError(
                "Dependency package has duplicate files or is missing the declared root"
            )
        for entry, parts in members:
            output = destination.joinpath(*parts)
            with archive.open(entry) as stream:
                copy_source(stream, output, check_interrupted)
    return root


def expired(request: ConversionRequest) -> bool:
    return request.expiresAt <= datetime.now(UTC)
