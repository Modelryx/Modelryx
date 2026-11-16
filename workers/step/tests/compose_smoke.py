import hashlib
import io
import json
import os
import struct
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

import numpy as np
import trimesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool


root = Path("/tmp/modelryx-step-worker-smoke")
root.mkdir(exist_ok=True)
step = root / "smoke-box.step"
writer = STEPControl_Writer()
writer.Transfer(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), STEPControl_AsIs)
assert writer.Write(str(step)) == IFSelect_RetDone

worker_key = os.environ["MODELRYX_INTERNAL_WORKER_KEY"]
base_url = os.getenv("STEP_WORKER_SMOKE_URL", "http://localhost:8080")
headers = {"X-Modelryx-Worker-Key": worker_key}


def submit_and_wait(filename: str, payload: bytes) -> dict:
    boundary = f"smoke-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="source"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base_url}/api/v1/jobs", data=body, method="POST",
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        job = json.load(response)
    for _ in range(180):
        if job["state"] not in {"QUEUED", "PROCESSING"}:
            break
        time.sleep(1)
        request = urllib.request.Request(f"{base_url}/api/v1/jobs/{job['id']}", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            job = json.load(response)
    assert job["state"] == "READY", job
    return job


request = urllib.request.Request(f"{base_url}/capabilities", headers=headers)
with urllib.request.urlopen(request, timeout=30) as response:
    capabilities = json.load(response)["capabilities"]
assert capabilities["component_bundle_to_glb"] is True, capabilities
assert "millimeter" in capabilities.get("component_bundle_input_units", []), capabilities

job = submit_and_wait("smoke-box.step", step.read_bytes())
assert job["worker_version"] == "2.0.0", job
outputs = {}
for name in ("glb", "metadata", "preview", "raw-metadata"):
    request = urllib.request.Request(
        f"{base_url}/api/v1/jobs/{job['id']}/{name}", headers=headers
    )
    with urllib.request.urlopen(request) as response:
        outputs[name] = response.read()

assert outputs["glb"][:4] == b"glTF" and len(outputs["glb"]) >= 12
metadata = json.loads(outputs["metadata"])
preview = json.loads(outputs["preview"])
raw_metadata = json.loads(outputs["raw-metadata"])
assert metadata["schema"] == "modelryx.part-metadata"
assert metadata["identity"]["name"] == "smoke-box"
assert preview["items"][0]["source"]["filename"] == "smoke-box.step"
assert raw_metadata == metadata
print(
    "STEP worker smoke passed: "
    f"{len(outputs['glb'])} GLB bytes and all metadata outputs"
)

# One blue solid with a red face proves face styles survive the real STEP-to-GLB path.
colored_step = root / "smoke-colored.step"
document = TDocStd_Document("Modelryx color smoke")
shape_tool = XCAFDoc_DocumentTool.ShapeTool(document.Main())
color_tool = XCAFDoc_DocumentTool.ColorTool(document.Main())
box = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
label = shape_tool.AddShape(box, False)
color_tool.SetColor(label, Quantity_Color(0.0, 0.0, 1.0, Quantity_TOC_RGB), XCAFDoc_ColorSurf)
face_explorer = TopExp_Explorer(box, TopAbs_FACE)
face_label = shape_tool.AddSubShape(label, face_explorer.Current())
color_tool.SetColor(face_label, Quantity_Color(1.0, 0.0, 0.0, Quantity_TOC_RGB), XCAFDoc_ColorSurf)
color_writer = STEPCAFControl_Writer()
color_writer.SetColorMode(True)
assert color_writer.Transfer(document, STEPControl_AsIs)
assert color_writer.Write(str(colored_step)) == IFSelect_RetDone
colored_job = submit_and_wait("smoke-colored.step", colored_step.read_bytes())
request = urllib.request.Request(f"{base_url}/api/v1/jobs/{colored_job['id']}/glb", headers=headers)
with urllib.request.urlopen(request) as response:
    colored_glb = response.read()
json_length, json_type = struct.unpack_from("<II", colored_glb, 12)
assert json_type == 0x4E4F534A
document = json.loads(colored_glb[20:20 + json_length])
binary_offset = 20 + json_length
binary_length, binary_type = struct.unpack_from("<II", colored_glb, binary_offset)
assert binary_type == 0x004E4942
binary = memoryview(colored_glb)[binary_offset + 8:binary_offset + 8 + binary_length]
color_arrays = []
for mesh in document["meshes"]:
    for primitive in mesh["primitives"]:
        accessor = document["accessors"][primitive["attributes"]["COLOR_0"]]
        assert accessor["componentType"] == 5121 and accessor["type"] == "VEC4"
        view = document["bufferViews"][accessor["bufferView"]]
        assert view["buffer"] == 0
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        color_arrays.append(np.ndarray(
            (accessor["count"], 4), dtype=np.uint8, buffer=binary, offset=offset,
            strides=(view.get("byteStride", 4), 1),
        )[:, :3])
colors = np.concatenate(color_arrays)
assert np.any((colors[:, 0] > 200) & (colors[:, 1] < 40) & (colors[:, 2] < 40)), "red face missing"
assert np.any((colors[:, 2] > 200) & (colors[:, 0] < 40) & (colors[:, 1] < 40)), "blue body missing"
print("STEP color smoke passed: body color and face override retained in GLB")

# Exercise the same shared-prototype STEP bundle contract emitted by external adapters.
components = [
    {"file": "shared.step", "partId": "box", "occurrenceId": identity, "nodeType": "part",
     "transformUnits": "millimeter", "transform": transform}
    for identity, transform in [
        ("assembly/box-a", [1, 0, 0, -50, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
        ("assembly/box-b", [0, -1, 0, 100, 1, 0, 0, -20, 0, 0, 1, 0, 0, 0, 0, 1]),
    ]
]
bundle = io.BytesIO()
with zipfile.ZipFile(bundle, "w") as archive:
    archive.writestr("manifest.json", json.dumps({"geometryUnits": "millimeter", "components": components}))
    archive.writestr("shared.step", step.read_bytes())
job = submit_and_wait("smoke-components.zip", bundle.getvalue())
request = urllib.request.Request(f"{base_url}/api/v1/jobs/{job['id']}/glb", headers=headers)
with urllib.request.urlopen(request, timeout=30) as response:
    data = response.read()
assert hashlib.sha256(data).hexdigest() == job["output_sha256"]
scene = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
np.testing.assert_allclose(scene.bounds, [[-0.05, -0.02, 0], [0.1, 0.02, 0.03]], atol=1e-6)
for component in components:
    world, geometry_name = scene.graph[component["occurrenceId"]]
    expected = np.diag([0.001, 0.001, 0.001, 1]) @ np.array(component["transform"]).reshape(4, 4)
    np.testing.assert_allclose(world, expected, atol=1e-7)
    assert len(scene.geometry[geometry_name].faces) > 0
document_length = int.from_bytes(data[12:16], "little")
document = json.loads(data[20:20 + document_length])
bindings = [node["extras"]["modelryx"] for node in document["nodes"] if "modelryx" in node.get("extras", {})]
assert {item["occurrenceId"] for item in bindings} == {item["occurrenceId"] for item in components}
assert all(item["partId"] == "box" and item["edgeSegments"] for item in bindings)
print("STEP assembly smoke passed: shared STEP geometry, exact IDs, negative/rotated placements and meter bounds")
