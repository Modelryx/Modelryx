"""Bounded validation of versioned external-worker bundles before generic publication."""

import json
import math
import stat
import zipfile
from pathlib import PurePosixPath


def validate_worker_bundle(path, role, max_bytes, max_pdf_bytes, *, contract=None):
    from modelryx.services.worker_output_policy import BUNDLE_CONTRACTS

    if role not in BUNDLE_CONTRACTS or contract != BUNDLE_CONTRACTS[role]:
        raise RuntimeError("Unsupported worker bundle contract")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if not 2 <= len(entries) <= 501:
            raise RuntimeError("Worker bundle has an invalid entry count")
        names = {}
        expanded = 0
        for entry in entries:
            name = entry.filename
            logical = PurePosixPath(name)
            if (
                not name
                or "\\" in name
                or ":" in name
                or logical.is_absolute()
                or len(logical.parts) != 1
                or name != logical.name
                or name in {".", ".."}
                or name.casefold() in names
                or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.external_attr & 0x400
                or entry.file_size <= 0
            ):
                raise RuntimeError("Worker bundle contains unsafe entries")
            names[name.casefold()] = entry
            expanded += entry.file_size
            if expanded > max_bytes:
                raise RuntimeError("Worker bundle exceeds the expanded size limit")
        manifest_entry = names.get("manifest.json")
        if manifest_entry is None or manifest_entry.file_size > 16 * 1024 * 1024:
            raise RuntimeError("Worker bundle requires a bounded manifest")
        manifest = json.loads(archive.read(manifest_entry))
        if not isinstance(manifest, dict):
            raise RuntimeError("Worker bundle manifest must be an object")
        key = {"drawing-representations": "drawings", "component-geometry": "components",
               "manufacturing-outputs": "files"}.get(role)
        records = manifest.get(key)
        if not isinstance(records, list) or not records or len(records) > 10000:
            raise RuntimeError("Worker bundle has invalid output records")
        used = {"manifest.json"}
        occurrences = set()
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("file"), str):
                raise RuntimeError("Worker bundle has an invalid file reference")
            filename = record["file"]
            entry = names.get(filename.casefold())
            if entry is None or filename != entry.filename or filename.casefold() == "manifest.json":
                raise RuntimeError("Worker bundle output is absent or ambiguous")
            extension = PurePosixPath(filename).suffix.casefold()
            if role == "drawing-representations":
                if filename.casefold() in used or extension != ".pdf" or entry.file_size > max_pdf_bytes:
                    raise RuntimeError("Worker drawing output is invalid or oversized")
                count = record.get("sheetCount")
                if type(count) is not int or count < 1:
                    raise RuntimeError("Worker drawing requires a positive sheet count")
                data = archive.read(entry)
                if not data.startswith(b"%PDF-") or not data[-8192:].rstrip().endswith(b"%%EOF"):
                    raise RuntimeError("Worker drawing PDF is incomplete")
            elif role == "component-geometry":
                occurrence = record.get("occurrenceId")
                transform = record.get("transform")
                if (
                    manifest.get("geometryUnits") != "millimeter"
                    or record.get("transformUnits") != "millimeter"
                    or record.get("nodeType") not in {"part", "assembly"}
                    or not isinstance(record.get("partId"), str) or not record["partId"]
                    or not isinstance(occurrence, str) or not occurrence or occurrence in occurrences
                    or not isinstance(transform, list) or len(transform) != 16
                    or any(type(value) not in {int, float} or not math.isfinite(value) for value in transform)
                    or transform[12:] != [0, 0, 0, 1]
                    or extension not in {".step", ".stp"}
                ):
                    raise RuntimeError("Worker component identity or placement is invalid")
                occurrences.add(occurrence)
                if filename.casefold() not in used:
                    with archive.open(entry) as stream:
                        head = stream.read(1024 * 1024)
                        tail = head[-8192:]
                        while chunk := stream.read(1024 * 1024):
                            tail = (tail + chunk)[-8192:]
                    if not head.lstrip().startswith(b"ISO-10303-21;") or b"END-ISO-10303-21;" not in tail:
                        raise RuntimeError("Worker component STEP is incomplete")
            elif role == "manufacturing-outputs":
                if filename.casefold() in used or extension not in {".json", ".dxf"}:
                    raise RuntimeError("Invalid Worker manufacturing output")
                if extension == ".json":
                    if entry.file_size > 16 * 1024 * 1024 or not isinstance(json.loads(archive.read(entry)), dict):
                        raise RuntimeError("Invalid Worker manufacturing metadata")
                else:
                    with archive.open(entry) as stream:
                        header = stream.read(512)
                    if not header.startswith(b"AutoCAD Binary DXF") and b"SECTION" not in header.upper():
                        raise RuntimeError("Invalid Worker flat-pattern DXF")
            used.add(filename.casefold())
        if used != set(names):
            raise RuntimeError("Worker bundle includes undeclared files")
        return manifest
