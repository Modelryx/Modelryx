import json
import struct
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from component_bundle_to_glb import main


class ComponentBundleUnitsTests(unittest.TestCase):
    def test_shared_step_tessellates_once_and_preserves_each_occurrence_appearance(self):
        geometry = trimesh.creation.box(extents=[100, 20, 10])
        converter = MagicMock()
        converter.tessellate_shape.return_value = (geometry.vertices, geometry.faces)
        converter.extract_edges.return_value = ([], [])
        edges = [0, 0, 0, 100, 0, 0]
        converter.edge_segments.return_value = edges
        factory = MagicMock(return_value=converter)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            components = [
                {"file": filename, "occurrenceId": f"placement-{index}", "partId": filename,
                 "color": color, "nodeType": "part", "transformUnits": "millimeter",
                 "transform": [1, 0, 0, index * 200, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
                for index, (filename, color) in enumerate([
                    ("shared.step", [1, 0, 0]), ("shared.step", [0, 1, 0]), ("other.step", [0, 0, 1])])
            ]
            bundle = root / "bundle.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"geometryUnits": "millimeter", "components": components}))
                for filename in ["shared.step", "other.step"]:
                    archive.writestr(filename, "ISO-10303-21;\nEND-ISO-10303-21;")
            output = root / "model.glb"
            # Substitute only the unavailable OCC boundary; use the real scene/GLB exporter.
            with patch.dict(sys.modules, {"step_to_glb": types.SimpleNamespace(StepToGlbConverter=factory)}), \
                    patch.object(sys, "argv", ["converter", str(bundle), "-o", str(output)]):
                main()
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(converter.read_step_file.call_count, 2)
            self.assertEqual(converter.tessellate_shape.call_count, 2)
            self.assertEqual(converter.extract_edges.call_count, 2)
            data = output.read_bytes()
            document = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            identities = {node["extras"]["modelryx"]["occurrenceId"]: node["extras"]["modelryx"]
                          for node in document["nodes"] if "modelryx" in node.get("extras", {})}
            self.assertEqual(set(identities), {item["occurrenceId"] for item in components})
            self.assertTrue(all(item["edgeSegments"] == edges for item in identities.values()))
            scene = trimesh.load(output, force="scene")
            for index, color in enumerate([[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255]]):
                transform, geometry_name = scene.graph[f"placement-{index}"]
                np.testing.assert_allclose(transform[:3, 3], [index * 0.2, 0, 0])
                np.testing.assert_array_equal(scene.geometry[geometry_name].visual.vertex_attributes["color"],
                                              np.tile(color, (len(geometry.vertices), 1)))

    def test_repeated_stl_is_loaded_once_per_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("part.stl", trimesh.creation.box().export(file_type="stl"))
                archive.writestr("manifest.json", json.dumps({"components": [
                    {"file": "part.stl", "occurrenceId": f"part-{index}"} for index in range(3)
                ]}))
            with patch("component_bundle_to_glb.trimesh.load", wraps=trimesh.load) as load, \
                    patch.object(sys, "argv", ["converter", str(bundle), "-o", str(root / "model.glb")]):
                main()
                self.assertEqual(load.call_count, 1)

    def test_declared_units_reject_ambiguous_or_invalid_placements_before_geometry(self):
        component = {"file": "part.stl", "occurrenceId": "placement", "partId": "prototype",
                     "transformUnits": "millimeter", "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
        for invalid in [
            [component, component],
            [{**component, "transform": [1] * 16}],
            [{**component, "transform": [float("nan"), *component["transform"][1:]]}],
        ]:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                bundle = Path(directory) / "invalid.zip"
                with zipfile.ZipFile(bundle, "w") as archive:
                    archive.writestr("manifest.json", json.dumps({"geometryUnits": "millimeter", "components": invalid}))
                with patch.object(sys, "argv", ["converter", str(bundle), "-o", str(Path(directory) / "model.glb")]):
                    with self.assertRaisesRegex(ValueError, "identity or world transform"):
                        main()

    def test_millimeter_bundle_preserves_repeated_identity_and_meter_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            geometry = trimesh.creation.box(extents=[100, 20, 10])
            manifest = {"geometryUnits": "millimeter", "components": [
                {"file": "part.stl", "occurrenceId": f"assembly/part-{index}", "partId": "prototype",
                 "nodeType": "part", "transformUnits": "millimeter",
                 "transform": [1, 0, 0, x, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
                for index, x in enumerate([-100, 100])
            ]}
            bundle = root / "bundle.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("part.stl", geometry.export(file_type="stl"))
            output = root / "model.glb"
            with patch.object(sys, "argv", ["component_bundle_to_glb", str(bundle), "-o", str(output)]):
                main()
            scene = trimesh.load(output, force="scene")
            np.testing.assert_allclose(scene.bounds, [[-0.15, -0.01, -0.005], [0.15, 0.01, 0.005]], atol=1e-7)
            data = output.read_bytes()
            length = struct.unpack_from("<I", data, 12)[0]
            document = json.loads(data[20:20 + length])
            identities = [node["extras"]["modelryx"] for node in document["nodes"] if "modelryx" in node.get("extras", {})]
            self.assertEqual({item["occurrenceId"] for item in identities}, {"assembly/part-0", "assembly/part-1"})
            self.assertTrue(all(item["partId"] == "prototype" for item in identities))


if __name__ == "__main__":
    unittest.main()
