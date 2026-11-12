"""Render a small, deterministic isometric PNG from a managed GLB.

This software renderer needs no display server or GPU. It deliberately samples
large meshes: the image is a visual index, while the GLB remains the preview.
"""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path

from PIL import Image, ImageDraw

THUMBNAIL_NAME = "revision-thumbnail.png"
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _multiply(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
        for row in range(4)
        for col in range(4)
    )


def _node_matrix(node: dict) -> tuple[float, ...]:
    if "matrix" in node:
        values = node["matrix"]
        if len(values) != 16:
            raise ValueError("Invalid GLB node matrix")
        return tuple(float(values[col * 4 + row]) for row in range(4) for col in range(4))
    x, y, z, w = (float(value) for value in node.get("rotation", [0, 0, 0, 1]))
    sx, sy, sz = (float(value) for value in node.get("scale", [1, 1, 1]))
    tx, ty, tz = (float(value) for value in node.get("translation", [0, 0, 0]))
    return (
        (1 - 2 * (y * y + z * z)) * sx,
        2 * (x * y - z * w) * sy,
        2 * (x * z + y * w) * sz,
        tx,
        2 * (x * y + z * w) * sx,
        (1 - 2 * (x * x + z * z)) * sy,
        2 * (y * z - x * w) * sz,
        ty,
        2 * (x * z - y * w) * sx,
        2 * (y * z + x * w) * sy,
        (1 - 2 * (x * x + y * y)) * sz,
        tz,
        0.0,
        0.0,
        0.0,
        1.0,
    )


def _transform(matrix: tuple[float, ...], point: tuple[float, ...]) -> tuple[float, ...]:
    x, y, z = point
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def _read_glb(path: Path) -> tuple[dict, memoryview]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF" or struct.unpack_from("<I", data, 4)[0] != 2:
        raise ValueError("Invalid GLB 2.0 file")
    if struct.unpack_from("<I", data, 8)[0] != len(data):
        raise ValueError("Invalid GLB length")
    chunks: dict[bytes, memoryview] = {}
    view = memoryview(data)
    offset = 12
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        if length > len(data) - offset:
            raise ValueError("Invalid GLB chunk length")
        chunks[kind] = view[offset : offset + length]
        offset += length
    if b"JSON" not in chunks or b"BIN\x00" not in chunks:
        raise ValueError("GLB lacks JSON or binary geometry")
    return json.loads(chunks[b"JSON"].tobytes().decode("utf-8")), chunks[b"BIN\x00"]


def _display_color(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        return None
    return tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))


def _preview_display_colors(path: Path | None) -> dict[str, tuple[float, float, float]]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, tuple[float, float, float]] = {}
    for item in document.get("items", []) if isinstance(document, dict) else []:
        if not isinstance(item, dict) or item.get("nodeType") == "reference":
            continue
        if re.fullmatch(r"origin\d*", str(item.get("name", "")), re.IGNORECASE):
            continue
        display = item.get("display")
        color = _display_color(display.get("color")) if isinstance(display, dict) else None
        if color is None:
            continue
        occurrence_id = item.get("occurrenceId")
        render_ids = item.get("renderOccurrenceIds")
        if isinstance(render_ids, list) and render_ids:
            ids = [value for value in render_ids if isinstance(value, str)]
        else:
            render_id = item.get("renderOccurrenceId", occurrence_id)
            ids = [render_id] if render_id == occurrence_id and isinstance(render_id, str) else []
        for render_id in ids:
            result[render_id] = color
    return result


def render_glb_thumbnail(
    source: Path,
    destination: Path,
    *,
    preview_path: Path | None = None,
    size: tuple[int, int] = (640, 400),
    max_triangles: int = 70_000,
) -> None:
    document, binary = _read_glb(source)
    accessors = document.get("accessors", [])
    views = document.get("bufferViews", [])
    meshes = document.get("meshes", [])
    nodes = document.get("nodes", [])
    materials = document.get("materials", [])
    preview_colors = _preview_display_colors(preview_path)

    def accessor(index: int, element: int, fmt: str, components: int) -> tuple:
        spec = accessors[index]
        if spec.get("sparse") or spec.get("bufferView") is None:
            raise ValueError("Sparse GLB accessors are unsupported for thumbnails")
        if element < 0 or element >= spec["count"]:
            raise ValueError("GLB accessor index is out of bounds")
        view = views[spec["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise ValueError("External GLB buffers are unsupported")
        element_size = struct.calcsize("<" + fmt * components)
        stride = view.get("byteStride", element_size)
        offset = view.get("byteOffset", 0) + spec.get("byteOffset", 0) + element * stride
        if stride < element_size or offset < 0 or offset + element_size > len(binary):
            raise ValueError("GLB accessor exceeds the binary chunk")
        return struct.unpack_from("<" + fmt * components, binary, offset)

    instances: list[tuple[dict, tuple[float, ...], tuple[float, float, float] | None]] = []

    def visit(
        node_index: int, parent: tuple[float, ...], ancestors: frozenset[int],
        inherited_color: tuple[float, float, float] | None = None,
        inherited_occurrence: str | None = None,
    ) -> None:
        if node_index in ancestors:
            raise ValueError("Cyclic GLB node hierarchy")
        node = nodes[node_index]
        matrix = _multiply(parent, _node_matrix(node))
        extras = node.get("extras")
        metadata = extras.get("modelryx") if isinstance(extras, dict) else None
        if isinstance(metadata, dict) and isinstance(metadata.get("partId"), str):
            display = metadata.get("display")
            inherited_color = (
                _display_color(display.get("color")) if isinstance(display, dict) else None
            )
            occurrence_id = metadata.get("occurrenceId")
            inherited_occurrence = occurrence_id if isinstance(occurrence_id, str) else None
        color = preview_colors.get(inherited_occurrence) or inherited_color
        if "mesh" in node:
            for primitive in meshes[node["mesh"]].get("primitives", []):
                if primitive.get("mode", 4) == 4 and "POSITION" in primitive.get("attributes", {}):
                    instances.append((primitive, matrix, color))
        for child in node.get("children", []):
            visit(child, matrix, ancestors | {node_index}, inherited_color, inherited_occurrence)

    scene_index = document.get("scene", 0)
    scenes = document.get("scenes", [])
    roots = scenes[scene_index].get("nodes", []) if scenes else range(len(nodes))
    for root in roots:
        visit(root, _IDENTITY, frozenset())

    total = sum(
        (
            accessors[p["indices"]]["count"]
            if "indices" in p
            else accessors[p["attributes"]["POSITION"]]["count"]
        )
        // 3
        for p, _, _ in instances
    )
    stride = max(1, math.ceil(total / max_triangles))
    triangles = []
    for primitive, matrix, display_color in instances:
        position_index = primitive["attributes"]["POSITION"]
        if accessors[position_index].get("componentType") != 5126:
            continue
        index_index = primitive.get("indices")
        index_fmt = {5121: "B", 5123: "H", 5125: "I"}.get(
            accessors[index_index]["componentType"] if index_index is not None else 0
        )
        if index_index is not None and index_fmt is None:
            continue
        count = (
            accessors[index_index]["count"]
            if index_index is not None
            else accessors[position_index]["count"]
        )
        material_index = primitive.get("material")
        color = [1.0, 1.0, 1.0]
        if isinstance(material_index, int) and material_index < len(materials):
            color = (
                materials[material_index]
                .get("pbrMetallicRoughness", {})
                .get("baseColorFactor", [*color, 1])[:3]
            )
        if display_color is not None:
            color = display_color
        for face in range(0, count // 3, stride):
            indices = [
                accessor(index_index, face * 3 + corner, index_fmt, 1)[0]
                if index_index is not None
                else face * 3 + corner
                for corner in range(3)
            ]
            points = tuple(
                _transform(matrix, accessor(position_index, index, "f", 3)) for index in indices
            )
            if all(math.isfinite(value) for point in points for value in point):
                triangles.append((points, color))
    if not triangles:
        raise ValueError("GLB has no renderable triangles")

    direction = (0.58, 0.58, 0.58)
    right = (0.70710678, 0.0, -0.70710678)
    up = (-0.40824829, 0.81649658, -0.40824829)

    def dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    projected = [
        (
            [(dot(point, right), dot(point, up)) for point in points],
            sum(dot(point, direction) for point in points) / 3,
            points,
            color,
        )
        for points, color in triangles
    ]
    xs = [point[0] for polygon, _, _, _ in projected for point in polygon]
    ys = [point[1] for polygon, _, _, _ in projected for point in polygon]
    width, height = size
    scale = min(
        width * 0.83 / max(max(xs) - min(xs), 1e-9), height * 0.82 / max(max(ys) - min(ys), 1e-9)
    )
    center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    image = Image.new("RGB", size, (247, 249, 252))
    draw = ImageDraw.Draw(image)
    light = (0.35, 0.8, 0.48)
    for polygon, _, points, color in sorted(projected, key=lambda item: item[1]):
        a, b, c = points
        edge1 = tuple(b[i] - a[i] for i in range(3))
        edge2 = tuple(c[i] - a[i] for i in range(3))
        normal = (
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0],
        )
        length = math.sqrt(dot(normal, normal))
        if length < 1e-12:
            continue
        shade = 0.58 + 0.34 * abs(dot(normal, light)) / length
        fill = tuple(max(0, min(255, round(float(channel) * 255 * shade))) for channel in color)
        pixels = [
            (round(width / 2 + (x - center_x) * scale), round(height / 2 - (y - center_y) * scale))
            for x, y in polygon
        ]
        draw.polygon(pixels, fill=fill)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
