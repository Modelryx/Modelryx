"""Deterministic hashes used before expensive CAD processing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def _normalized_path(path: str) -> str:
    value = path.replace("\\", "/").strip("/")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"invalid package path: {path!r}")
    return "/".join(parts)


def canonical_package_sha256(files: Iterable[tuple[str, str]]) -> str:
    """Hash a logical package manifest, independent of archive compression/order."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path, digest in files:
        normalized = _normalized_path(path)
        digest = digest.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid SHA-256 for {path!r}")
        key = normalized.casefold()
        if key in seen:
            raise ValueError(f"duplicate package path: {normalized!r}")
        seen.add(key)
        entries.append((normalized, digest))
    if not entries:
        raise ValueError("a package must contain at least one file")
    manifest = "\n".join(f"{path}:{digest}" for path, digest in sorted(entries))
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()
