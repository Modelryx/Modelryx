import json
import struct
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "converter"))
from glb_utils import make_materials_double_sided


def glb(document: dict, binary: bytes = b"\0\0\0\0") -> bytes:
    encoded = json.dumps(document).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    body = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    body += struct.pack("<II", len(binary), 0x004E4942) + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def document(value: bytes) -> dict:
    length, kind = struct.unpack_from("<II", value, 12)
    assert kind == 0x4E4F534A
    return json.loads(value[20:20 + length].rstrip(b" ").decode("utf-8"))


class DoubleSidedGlbTests(unittest.TestCase):
    def test_millimeter_scene_scale_preserves_local_identity_and_binary(self):
        node = {"mesh": 0, "translation": [125, -20, 0], "extras": {"modelryx": {
            "partId": "wheel", "occurrenceId": "assembly/wheel:1", "edgeSegments": [0, 0, 0, 100, 0, 0]}}}
        original = glb({"asset": {"version": "2.0"}, "scene": 0,
                        "scenes": [{"nodes": [0]}], "nodes": [node]})
        converted = make_materials_double_sided(original, source_units="millimeter")
        output = document(converted)
        self.assertEqual(output["nodes"][0], node)
        root = output["nodes"][output["scenes"][0]["nodes"][0]]
        self.assertEqual(root, {"children": [0], "scale": [0.001] * 3})
        self.assertEqual(converted[-12:], original[-12:])
        self.assertAlmostEqual(root["scale"][0] * (125 + 100), 0.225)

    def test_legacy_scene_is_not_rescaled_without_units(self):
        output = document(make_materials_double_sided(glb({
            "asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}]})))
        self.assertEqual(output["scenes"][0]["nodes"], [0])
        self.assertEqual(len(output["nodes"]), 1)

    def test_unknown_units_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported component geometry units"):
            make_materials_double_sided(glb({"asset": {"version": "2.0"}}), source_units="furlong")

    def test_adds_material_to_unmaterialed_triangles(self):
        output = document(make_materials_double_sided(glb({
            "asset": {"version": "2.0"},
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        })))
        self.assertTrue(output["materials"][0]["doubleSided"])
        self.assertEqual(output["meshes"][0]["primitives"][0]["material"], 0)

    def test_updates_existing_material_without_assigning_lines(self):
        output = document(make_materials_double_sided(glb({
            "asset": {"version": "2.0"},
            "materials": [{"name": "Existing"}],
            "meshes": [{"primitives": [
                {"attributes": {"POSITION": 0}, "material": 0},
                {"attributes": {"POSITION": 1}, "mode": 1},
            ]}],
        })))
        self.assertTrue(output["materials"][0]["doubleSided"])
        self.assertNotIn("material", output["meshes"][0]["primitives"][1])


if __name__ == "__main__":
    unittest.main()
