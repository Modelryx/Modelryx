"""Small GLB transformations shared by the geometry converters."""

import json
import struct
from pathlib import Path


JSON_CHUNK = 0x4E4F534A


def make_materials_double_sided(glb: bytes, *, source_units: str | None = None) -> bytes:
    """Return a GLB whose triangle primitives render from both face orientations."""
    if len(glb) < 20 or glb[:4] != b"glTF" or struct.unpack_from("<I", glb, 4)[0] != 2:
        raise ValueError("invalid GLB 2.0 payload")

    chunks: list[tuple[int, bytes]] = []
    offset = 12
    while offset + 8 <= len(glb):
        length, kind = struct.unpack_from("<II", glb, offset)
        offset += 8
        end = offset + length
        if end > len(glb):
            raise ValueError("invalid GLB chunk length")
        chunks.append((kind, glb[offset:end]))
        offset = end
    if offset != len(glb):
        raise ValueError("invalid GLB trailing data")

    try:
        json_index = next(index for index, (kind, _) in enumerate(chunks) if kind == JSON_CHUNK)
    except StopIteration as error:
        raise ValueError("GLB has no JSON chunk") from error

    document = json.loads(chunks[json_index][1].rstrip(b" \t\r\n\0").decode("utf-8"))
    if source_units is not None:
        if source_units != "millimeter":
            raise ValueError("unsupported component geometry units")
        # Keep mesh coordinates, occurrence transforms and native edge extras in the same local
        # units. A scene parent converts all of them together to glTF's meter world coordinates.
        nodes = document.setdefault("nodes", [])
        original_count = len(nodes)
        for scene in document.get("scenes", []):
            roots = scene.get("nodes", [])
            if not isinstance(roots, list) or any(type(index) is not int or index < 0 or index >= original_count for index in roots):
                raise ValueError("invalid component scene roots")
            if roots:
                scene["nodes"] = [len(nodes)]
                nodes.append({"children": roots, "scale": [0.001, 0.001, 0.001]})
    materials = document.setdefault("materials", [])
    for material in materials:
        material["doubleSided"] = True

    default_material = None
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            # glTF mode 4 (TRIANGLES) is the default when mode is omitted.
            if primitive.get("mode", 4) != 4:
                continue
            material_index = primitive.get("material")
            if isinstance(material_index, int) and 0 <= material_index < len(materials):
                materials[material_index]["doubleSided"] = True
                continue
            if default_material is None:
                default_material = len(materials)
                materials.append({"name": "Modelryx Double-Sided", "doubleSided": True})
            primitive["material"] = default_material

    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    chunks[json_index] = (JSON_CHUNK, encoded)

    body = b"".join(struct.pack("<II", len(payload), kind) + payload for kind, payload in chunks)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def export_double_sided_glb(value, output_path: str | Path, *, source_units: str | None = None) -> None:
    """Export a trimesh mesh/scene as a double-sided GLB."""
    glb = value.export(file_type="glb")
    Path(output_path).write_bytes(make_materials_double_sided(glb, source_units=source_units))
