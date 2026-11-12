import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCC.Core.Geom import Geom_BezierCurve
from OCC.Core.TColgp import TColgp_Array1OfPnt
from OCC.Core.gp import gp_Pnt


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

from component_bundle_to_glb import (  # noqa: E402
    component_color,
    component_transform,
    main,
    step_file_color,
)
from step_to_glb import StepToGlbConverter  # noqa: E402


class ComponentColorTests(unittest.TestCase):
    def test_autocad_matrix_preserves_millimeter_translation(self):
        transform = component_transform({
            "occurrenceId": "assembly/body",
            "transformUnits": "millimeter",
            "transform": [
                0, -1, 0, 125,
                1, 0, 0, 45,
                0, 0, 1, 20,
                0, 0, 0, 1,
            ],
        })

        np.testing.assert_allclose(transform[:3, 3], np.array([125, 45, 20]))

    def test_bundle_preserves_occurrence_ids_in_glb_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            geometry = root / "component.stl"
            geometry.write_text(
                "solid component\n"
                "facet normal 0 0 1\nouter loop\n"
                "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
                "endloop\nendfacet\nendsolid component\n",
                encoding="ascii",
            )
            manifest = {
                "components": [
                    {
                        "occurrenceId": "frame/beam:1",
                        "partId": "beam",
                        "file": geometry.name,
                        "translation": [0, 0, 0],
                    }
                ]
            }
            bundle = root / "components.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.write(geometry, geometry.name)
            output = root / "preview.glb"

            with patch.object(sys, "argv", ["component_bundle_to_glb", str(bundle), "-o", str(output)]):
                main()

            data = output.read_bytes()
            json_length, json_type = struct.unpack_from("<I4s", data, 12)
            self.assertEqual(json_type, b"JSON")
            document = json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" \0"))
            matching_nodes = [node for node in document["nodes"] if node.get("name") == "frame/beam:1"]
            self.assertEqual(len(matching_nodes), 1)
            self.assertEqual(
                matching_nodes[0]["extras"]["modelryx"]["occurrenceId"],
                "frame/beam:1",
            )

    def test_samples_long_curve_by_deflection_instead_of_parameter_span(self):
        poles = TColgp_Array1OfPnt(1, 4)
        poles.SetValue(1, gp_Pnt(0, 0, 0))
        poles.SetValue(2, gp_Pnt(0, 100, 0))
        poles.SetValue(3, gp_Pnt(100, -100, 0))
        poles.SetValue(4, gp_Pnt(100, 0, 0))
        edge = BRepBuilderAPI_MakeEdge(Geom_BezierCurve(poles)).Edge()

        points = StepToGlbConverter(tessellation_quality=0.5).discretize_edge(edge)

        self.assertGreater(len(points), 3)
        self.assertEqual(points[0], [0.0, 0.0, 0.0])
        self.assertEqual(points[-1], [100.0, 0.0, 0.0])

    def test_flattens_native_edge_indices_into_segment_coordinates(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], dtype=float)
        indices = np.array([[0, 1], [1, 2]], dtype=int)

        self.assertEqual(
            StepToGlbConverter.edge_segments(vertices, indices),
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0],
        )

    def test_converts_normalized_rgba_to_bytes(self):
        np.testing.assert_array_equal(
            component_color({"color": [1.0, 0.25, 0.0, 0.5]}),
            np.array([255, 64, 0, 128], dtype=np.uint8),
        )

    def test_defaults_rgb_alpha_and_clamps_values(self):
        np.testing.assert_array_equal(
            component_color({"color": [-1.0, 0.5, 2.0]}),
            np.array([0, 128, 255, 255], dtype=np.uint8),
        )

    def test_rejects_invalid_color(self):
        self.assertIsNone(component_color({"color": [1.0, "red", 0.0]}))
        self.assertIsNone(component_color({"color": [1.0, 0.0]}))

    def test_reads_most_distinctive_inventor_step_color(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "colored.step"
            source.write_text(
                "#1=COLOUR_RGB('',0.733333,0.733333,0.733333);\n"
                "#2=COLOUR_RGB('',0.8,0.,0.);\n",
                encoding="ascii",
            )
            np.testing.assert_array_equal(
                step_file_color(source),
                np.array([204, 0, 0, 255], dtype=np.uint8),
            )


if __name__ == "__main__":
    unittest.main()
