"""Generate real FreeCAD fixtures and verify the complete FCStd -> PFG pipeline."""

# FreeCAD rewrites module globals during initialization, so it must load before stdlib imports.
# ruff: noqa: I001

import FreeCAD  # type: ignore
import Draft  # type: ignore
import Part  # type: ignore
import Sketcher  # type: ignore
import JointObject  # type: ignore

import json
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path

import trimesh
from metadata_contract import validate_part_metadata
import fcstd_to_modelryx_glb as converter
from link_array_geometry import filtered_expanded_array_shape
from fcstd_to_modelryx_glb import (
    convert_fcstd_to_modelryx_package,
    extract_fcstd_part_metadata,
)
from techdraw_extractor import _valid_3d_references


def make_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("ModelryxGoldenAssembly")
    try:
        assembly = document.addObject("App::Part", "Assembly")
        assembly.Label = "Golden Assembly"
        assembly.addProperty("App::PropertyString", "PartNumber", "Modelryx")
        assembly.PartNumber = "ASM-100"
        assembly.addProperty("App::PropertyString", "Revision", "Modelryx")
        assembly.Revision = "B"
        assembly.addProperty("App::PropertyString", "Description", "Modelryx")
        assembly.Description = "Golden test assembly"
        assembly.addProperty("App::PropertyStringList", "Tags", "Modelryx")
        assembly.Tags = ["golden", "fixture"]
        shared_shape = Part.makeBox(20, 10, 5)
        first = document.addObject("Part::Feature", "Bracket001")
        first.Label = "Bracket 1"
        first.Shape = shared_shape
        first.addProperty("App::PropertyString", "Material", "Modelryx")
        first.Material = "Steel"
        first.addProperty("App::PropertyFloat", "DensityKgM3", "Modelryx")
        first.DensityKgM3 = 7850
        second = document.addObject("Part::Feature", "Bracket002")
        second.Label = "Bracket 2"
        second.Shape = shared_shape
        second.addProperty("App::PropertyString", "Material", "Modelryx")
        second.Material = "Steel"
        second.addProperty("App::PropertyFloat", "DensityKgM3", "Modelryx")
        second.DensityKgM3 = 7850
        second.Placement.Base = FreeCAD.Vector(30, 0, 0)
        assembly.addObject(first)
        assembly.addObject(second)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_asymmetric_compound_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("AsymmetricCompound")
    try:
        component = document.addObject("Part::Feature", "CompoundComponent")
        component.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(20, 10, 10, FreeCAD.Vector(20, 0, 0)),
            ]
        )
        component.addProperty("App::PropertyString", "Material", "Modelryx")
        component.Material = "Steel"
        component.addProperty("App::PropertyFloat", "DensityKgM3", "Modelryx")
        component.DensityKgM3 = 7850
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_partial_material_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("PartialMaterial")
    try:
        group = document.addObject("App::Part", "Assembly")
        for index in range(2):
            part = document.addObject("Part::Feature", f"Part{index}")
            part.Shape = Part.makeBox(10, 10, 10)
            part.Placement.Base = FreeCAD.Vector(index * 15, 0, 0)
            if index == 0:
                part.addProperty("App::PropertyString", "Material", "Modelryx")
                part.Material = "Steel"
            group.addObject(part)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_native_density_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("NativeDensity")
    try:
        part = document.addObject("Part::Feature", "Part")
        part.Shape = Part.makeBox(10, 10, 10)
        part.addProperty("App::PropertyString", "Material", "Modelryx")
        part.Material = "Steel"
        part.addProperty("App::PropertyDensity", "Density", "Modelryx")
        part.Density = FreeCAD.Units.Quantity("7.85 g/cm^3")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_parameter_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("ModelryxParameterFixture")
    try:
        model = document.addObject("Part::Feature", "ConfiguredBlock")
        model.Shape = Part.makeBox(25, 12.5, 5)
        model.addProperty("App::PropertyString", "Configuration", "Design")
        model.Configuration = "Production"
        model.addProperty("App::PropertyLength", "DrivenLength", "Design")
        spreadsheet = document.addObject("Spreadsheet::Sheet", "Parameters")
        spreadsheet.set("A1", "12.5 mm")
        spreadsheet.setAlias("A1", "Thickness")
        spreadsheet.set("A2", "=Thickness * 2")
        spreadsheet.setAlias("A2", "OverallLength")
        model.setExpression("DrivenLength", "Parameters.Thickness")
        variants = document.addObject("App::VarSet", "DesignVariants")
        variants.addProperty("App::PropertyEnumeration", "Configuration", "Variants")
        variants.Configuration = ["Default", "Machined", "Lightweight"]
        variants.Configuration = "Machined"
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_labeled_expression_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("LabeledExpression")
    try:
        source = document.addObject("App::FeaturePython", "DesignSource")
        source.Label = "Design Plate"
        source.addProperty("App::PropertyLength", "Thickness", "Design")
        source.Thickness = 8
        box = document.addObject("Part::Box", "Box")
        box.setExpression("Width", "<<Design Plate>>.Thickness")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_sketch_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("SketchSemantics")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "DesignSketch")
        sketch.Label = "Mounting profile"
        line = sketch.addGeometry(
            Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0)),
            False,
        )
        circle = sketch.addGeometry(
            Part.Circle(FreeCAD.Vector(5, 5, 0), FreeCAD.Vector(0, 0, 1), 2), True
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        distance = sketch.addConstraint(Sketcher.Constraint("Distance", line, 10.0))
        sketch.renameConstraint(distance, "LengthDrive")
        parameters = document.addObject("Spreadsheet::Sheet", "Parameters")
        parameters.set("A1", "10 mm")
        parameters.setAlias("A1", "Length")
        sketch.setExpression("Constraints.LengthDrive", "Parameters.Length")
        sketch.addConstraint(Sketcher.Constraint("Radius", circle, 2))
        target = document.addObject("App::FeaturePython", "DrivenTarget")
        target.addProperty("App::PropertyLength", "RadiusValue", "Design")
        target.setExpression("RadiusValue", "DesignSketch.Constraints[2]")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_sketch_constraint_state_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("SketchConstraintStates")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Sketch")
        first = sketch.addGeometry(Part.Circle(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 3), False)
        second = sketch.addGeometry(Part.Circle(FreeCAD.Vector(10, 0, 0), FreeCAD.Vector(0, 0, 1), 4), False)
        reference = sketch.addConstraint(Sketcher.Constraint("Radius", first, 3))
        inactive = sketch.addConstraint(Sketcher.Constraint("Radius", second, 4))
        sketch.setDriving(reference, False)
        sketch.setActive(inactive, False)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_pocket_construction_fixture(path: Path, *, annular: bool = False) -> None:
    document = FreeCAD.newDocument("PocketConstruction")
    try:
        body = document.addObject("PartDesign::Body", "Body")
        block = body.newObject("PartDesign::AdditiveBox", "Block")
        block.Length = 30
        block.Width = 20
        block.Height = 6
        document.recompute()
        sketch = body.newObject("Sketcher::SketchObject", "PocketSketch")
        sketch.Placement.Base = FreeCAD.Vector(0, 0, 6)
        sketch.addGeometry(Part.Circle(FreeCAD.Vector(8, 10, 0), FreeCAD.Vector(0, 0, 1), 2), False)
        sketch.addGeometry(
            Part.Circle(FreeCAD.Vector(8 if annular else 20, 10, 0), FreeCAD.Vector(0, 0, 1), 3),
            not annular,
        )
        pocket = body.newObject("PartDesign::Pocket", "Pocket")
        pocket.Profile = sketch
        pocket.Length = 2
        document.recompute()
        assert not pocket.Shape.isNull()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_feature_after_tip_fixture(path: Path, *, no_tip: bool = False) -> None:
    document = FreeCAD.newDocument("FeatureAfterTip")
    try:
        body = document.addObject("PartDesign::Body", "Body")
        base = body.newObject("PartDesign::AdditiveBox", "Base")
        base.Length = base.Width = base.Height = 20
        document.recompute()
        cut = body.newObject("PartDesign::SubtractiveCylinder", "FutureCut")
        cut.Radius = 2
        cut.Height = 20
        cut.Placement.Base = FreeCAD.Vector(10, 10, 0)
        document.recompute()
        body.Tip = None if no_tip else base
        document.recompute()
        if no_tip:
            assert body.Tip is None
        else:
            assert body.Tip == base
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_missing_profile_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("MissingProfile")
    try:
        body = document.addObject("PartDesign::Body", "Body")
        body.newObject("PartDesign::Pad", "BrokenPad")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_angular_sketch_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("AngularSketch")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Sketch")
        line = sketch.addGeometry(
            Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 10, 0)), False
        )
        sketch.addConstraint(Sketcher.Constraint("Angle", line, 0.7853981633974483))
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_attached_sketch_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("AttachedSketch")
    try:
        base = document.addObject("Part::Box", "SupportBox")
        sketch = document.addObject("Sketcher::SketchObject", "AttachedSketch")
        document.recompute()
        sketch.AttachmentSupport = [(base, ["Face1"])]
        sketch.MapMode = "FlatFace"
        sketch.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, 2), FreeCAD.Rotation())
        sketch.addExternal("SupportBox", "Edge1")
        sketch.addExternal("SupportBox", "Edge2")
        line = sketch.addGeometry(
            Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0)), False
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        sketch.addConstraint(Sketcher.Constraint("PointOnObject", line, 1, -4))
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_invalid_sketch_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("InvalidSketch")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "InvalidSketch")
        line = sketch.addGeometry(
            Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0)), False
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        sketch.addConstraint(Sketcher.Constraint("Vertical", line))
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_unresolved_sketch_support_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("UnresolvedSketchSupport")
    try:
        base = document.addObject("Part::Box", "SupportBox")
        sketch = document.addObject("Sketcher::SketchObject", "AttachedSketch")
        document.recompute()
        sketch.AttachmentSupport = [(base, ["Face1"])]
        sketch.MapMode = "FlatFace"
        document.recompute()
        document.removeObject("SupportBox")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_feature_semantics_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("FeatureSemantics")
    try:
        box = document.addObject("Part::Box", "NativeBox")
        box.Length = 30
        box.Width = 12
        box.Height = 6
        cylinder = document.addObject("Part::Cylinder", "NativeCylinder")
        cylinder.Radius = 4
        cylinder.Height = 15
        cylinder.Angle = 180
        cylinder.Placement.Base = FreeCAD.Vector(40, 0, 0)
        cone = document.addObject("Part::Cone", "NativeCone")
        cone.Radius1 = 6
        cone.Radius2 = 2
        cone.Height = 18
        cone.Angle = 270
        cone.Placement.Base = FreeCAD.Vector(55, 0, 0)
        sphere = document.addObject("Part::Sphere", "NativeSphere")
        sphere.Radius = 7
        sphere.Angle1 = -45
        sphere.Angle2 = 60
        sphere.Angle3 = 240
        sphere.Placement.Base = FreeCAD.Vector(75, 0, 0)
        torus = document.addObject("Part::Torus", "NativeTorus")
        torus.Radius1 = 10
        torus.Radius2 = 3
        torus.Angle3 = 300
        torus.Placement.Base = FreeCAD.Vector(100, 0, 0)
        prism = document.addObject("Part::Prism", "NativePrism")
        prism.Polygon = 6
        prism.Circumradius = 5
        prism.Height = 11
        prism.FirstAngle = 15
        prism.SecondAngle = 75
        prism.Placement.Base = FreeCAD.Vector(125, 0, 0)
        wedge = document.addObject("Part::Wedge", "NativeWedge")
        wedge.Xmin = 0
        wedge.Xmax = 12
        wedge.X2min = 2
        wedge.X2max = 10
        wedge.Ymin = 0
        wedge.Ymax = 8
        wedge.Zmin = 0
        wedge.Zmax = 6
        wedge.Z2min = 1
        wedge.Z2max = 5
        wedge.Placement.Base = FreeCAD.Vector(145, 0, 0)
        ellipsoid = document.addObject("Part::Ellipsoid", "NativeEllipsoid")
        ellipsoid.Radius1 = 8
        ellipsoid.Radius2 = 6
        ellipsoid.Radius3 = 4
        ellipsoid.Angle1 = -60
        ellipsoid.Angle2 = 70
        ellipsoid.Angle3 = 270
        ellipsoid.Placement.Base = FreeCAD.Vector(170, 0, 0)
        array_base = document.addObject("Part::Box", "ArrayBase")
        array_base.Length = 4
        array_base.Width = 3
        array_base.Height = 2
        array_base.Placement.Base = FreeCAD.Vector(200, 0, 0)
        orthogonal_array = Draft.make_ortho_array(
            array_base,
            FreeCAD.Vector(10, 0, 0),
            FreeCAD.Vector(0, 12, 0),
            FreeCAD.Vector(0, 0, 5),
            2,
            3,
            1,
        )
        orthogonal_array.Label = "Orthogonal Array"
        polar_base = document.addObject("Part::Cylinder", "PolarBase")
        polar_base.Radius = 2
        polar_base.Height = 3
        polar_base.Placement.Base = FreeCAD.Vector(230, 0, 0)
        polar_array = Draft.make_polar_array(polar_base, 4, 180)
        polar_array.Label = "Polar Array"
        circular_base = document.addObject("Part::Box", "CircularBase")
        circular_base.Length = 3
        circular_base.Width = 2
        circular_base.Height = 1
        circular_base.Placement.Base = FreeCAD.Vector(260, 0, 0)
        circular_array = Draft.make_circular_array(
            circular_base,
            r_distance=15,
            tan_distance=20,
            number=2,
            symmetry=1,
            axis=FreeCAD.Vector(0, 1, 0),
            center=FreeCAD.Vector(4, 5, 6),
        )
        circular_array.Label = "Circular Array"
        array_path = document.addObject("Part::Feature", "ArrayPath")
        array_path.Shape = Part.makePolygon(
            [
                FreeCAD.Vector(0, 0, 0),
                FreeCAD.Vector(20, 0, 0),
                FreeCAD.Vector(20, 20, 0),
            ]
        )
        path_array = Draft.make_path_array(array_base, array_path, count=5, align=True)
        path_array.Label = "Path Array"
        path_array.StartOffset = 1
        path_array.EndOffset = 2
        point_source = document.addObject("Part::Feature", "ArrayPoints")
        point_source.Shape = Part.makeCompound(
            [
                Part.Vertex(FreeCAD.Vector(0, 0, 0)),
                Part.Vertex(FreeCAD.Vector(10, 0, 0)),
                Part.Vertex(FreeCAD.Vector(10, 10, 0)),
            ]
        )
        point_array = Draft.make_point_array(polar_base, point_source)
        point_array.Label = "Point Array"
        profile = document.addObject("Part::Feature", "ExtrusionProfile")
        profile.Shape = Part.makePolygon(
            [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(12, 0, 0), FreeCAD.Vector(12, 4, 0)]
        )
        extrusion = document.addObject("Part::Extrusion", "NativeExtrusion")
        extrusion.Base = profile
        extrusion.Dir = FreeCAD.Vector(0, 0, 8)
        extrusion.LengthFwd = 8

        base = document.addObject("Part::Feature", "BooleanBase")
        base.Shape = Part.makeBox(20, 20, 8)
        tool = document.addObject("Part::Feature", "BooleanTool")
        tool.Shape = Part.makeCylinder(3, 8, FreeCAD.Vector(10, 10, 0))
        cut = document.addObject("Part::Cut", "NativeCut")
        cut.Base = base
        cut.Tool = tool

        loft_lower = document.addObject("Part::Feature", "LoftZLowerSection")
        loft_lower.Shape = Part.makePolygon(
            [
                FreeCAD.Vector(280, 0, 0),
                FreeCAD.Vector(292, 0, 0),
                FreeCAD.Vector(292, 12, 0),
                FreeCAD.Vector(280, 12, 0),
                FreeCAD.Vector(280, 0, 0),
            ]
        )
        loft_upper = document.addObject("Part::Feature", "LoftAUpperSection")
        loft_upper.Shape = Part.makePolygon(
            [
                FreeCAD.Vector(283, 3, 15),
                FreeCAD.Vector(289, 3, 15),
                FreeCAD.Vector(289, 9, 15),
                FreeCAD.Vector(283, 9, 15),
                FreeCAD.Vector(283, 3, 15),
            ]
        )
        loft = document.addObject("Part::Loft", "NativeLoft")
        loft.Sections = [loft_lower, loft_upper]
        loft.Solid = True
        loft.Ruled = True
        loft.Closed = False
        loft.Linearize = True
        loft.MaxDegree = 3
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_native_assembly_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("NativeAssemblySemantics")
    try:
        assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
        joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        first = assembly.newObject("Part::Box", "BasePlate")
        second = assembly.newObject("Part::Box", "Arm")
        second.Placement.Base = FreeCAD.Vector(10, 0, 0)
        revolute = joint_group.newObject("App::FeaturePython", "Pivot")
        JointObject.Joint(revolute, 1)
        revolute.Reference1 = (first, ["Face1"])
        revolute.Reference2 = (second, ["Face2"])
        revolute.AngleMin = -45
        revolute.AngleMax = 45
        revolute.EnableAngleMin = True
        revolute.EnableAngleMax = True
        fixed = joint_group.newObject("App::FeaturePython", "FixedPair")
        JointObject.Joint(fixed, 0)
        fixed.Reference1 = (first, ["Face1"])
        fixed.Reference2 = (second, ["Face2"])
        grounded = joint_group.newObject("App::FeaturePython", "GroundBase")
        JointObject.GroundedJoint(grounded, first)
        links = []
        for name in ("ArmLink1", "ArmLink2"):
            link = assembly.newObject("App::Link", name)
            link.setLink(second)
            links.append(link)
        linked_joint = joint_group.newObject("App::FeaturePython", "LinkedPair")
        JointObject.Joint(linked_joint, 0)
        linked_joint.Reference1 = (links[0], ["Face1"])
        linked_joint.Reference2 = (links[1], ["Face2"])
        nested = assembly.newObject("App::Part", "Nested")
        nested_link = nested.newObject("App::Link", "NestedArm")
        nested_link.setLink(second)
        nested_joint = joint_group.newObject("App::FeaturePython", "NestedPair")
        JointObject.Joint(nested_joint, 0)
        nested_joint.Reference1 = (nested, ["NestedArm.Face1"])
        nested_joint.Reference2 = (links[0], ["Face2"])
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_solver_observation_fixture(path: Path, joint_index: int, *, conflicting: bool = False) -> None:
    document = FreeCAD.newDocument("SolverObservation")
    try:
        assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
        group = assembly.newObject("Assembly::JointGroup", "Joints")
        base = assembly.newObject("Part::Box", "Base")
        moving = assembly.newObject("Part::Box", "Moving")
        document.recompute()
        ground = group.newObject("App::FeaturePython", "Ground")
        JointObject.GroundedJoint(ground, base)
        joint = group.newObject("App::FeaturePython", "Joint")
        JointObject.Joint(joint, joint_index)
        joint.Reference1 = (base, ["Face1"])
        joint.Reference2 = (moving, ["Face2"])
        if conflicting:
            other = group.newObject("App::FeaturePython", "ConflictingJoint")
            JointObject.Joint(other, 0)
            other.Reference1 = (base, ["Face1"])
            other.Reference2 = (moving, ["Face2"])
            other.Offset1 = FreeCAD.Placement(FreeCAD.Vector(10, 0, 0), FreeCAD.Rotation())
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_reference_geometry_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("ReferenceGeometrySemantics")
    try:
        body = document.addObject("PartDesign::Body", "Body")
        solid = body.newObject("PartDesign::AdditiveBox", "Solid")
        solid.Length = 20
        solid.Width = 10
        solid.Height = 5
        plane = body.newObject("PartDesign::Plane", "InspectionPlane")
        plane.Label = "Inspection plane"
        plane.Placement.Base = FreeCAD.Vector(5, 2, 1)
        axis = body.newObject("PartDesign::Line", "ToolAxis")
        axis.Label = "Tool axis"
        point = body.newObject("PartDesign::Point", "FixturePoint")
        point.Label = "Fixture point"
        coordinate_system = body.newObject("PartDesign::CoordinateSystem", "MachiningLCS")
        coordinate_system.Label = "Machining coordinate system"
        coordinate_system.Placement.Base = FreeCAD.Vector(10, 5, 0)
        body.Tip = solid
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_external_link_fixture(root: Path) -> Path:
    component_path = root / "linked-component.FCStd"
    component = FreeCAD.newDocument("LinkedComponent")
    try:
        body = component.addObject("PartDesign::Feature", "Body")
        body.Label = "Linked spacer"
        body.Shape = Part.makeCylinder(5, 12)
        component.recompute()
        component.saveAs(str(component_path))

        assembly_path = root / "linked-assembly.FCStd"
        assembly_document = FreeCAD.newDocument("LinkedAssembly")
        assembly_document.saveAs(str(assembly_path))
        assembly = assembly_document.addObject("App::Part", "Assembly")
        for index, x_position in enumerate((0, 20), start=1):
            link = assembly_document.addObject("App::Link", f"Spacer{index}")
            link.Label = f"Spacer {index}"
            link.LinkedObject = body
            link.addProperty("App::PropertyString", "Configuration", "Design")
            link.Configuration = "Long" if index == 1 else "Short"
            link.Placement.Base = FreeCAD.Vector(x_position, 0, 0)
            assembly.addObject(link)
        assembly_document.recompute()
        assembly_document.saveAs(str(assembly_path))
        FreeCAD.closeDocument(assembly_document.Name)
        return assembly_path
    finally:
        FreeCAD.closeDocument(component.Name)


def make_link_array_fixture(
    path: Path,
    *,
    expanded: bool = False,
    transformed: bool = False,
    unknown_parent: bool = False,
    suppressed_element: bool = False,
    suppressed_array: bool = False,
    explicit_element_states: bool = False,
    all_elements_suppressed: bool = False,
    extra_link: bool = False,
    variant: bool = False,
    copy_on_change: bool = False,
    collapse_after_suppression: bool = False,
    unrelated_selector: bool = False,
    overlap_active: bool = False,
    assigned_material: bool = False,
) -> None:
    document = FreeCAD.newDocument("LinkArrayEvidence")
    try:
        source = document.addObject("Part::Feature", "ArraySource")
        source.Shape = Part.makeBox(2, 3, 4)
        if assigned_material:
            source.addProperty("App::PropertyString", "Material", "Modelryx")
            source.Material = "Steel"
            source.addProperty("App::PropertyFloat", "DensityKgM3", "Modelryx")
            source.DensityKgM3 = 7850
        if copy_on_change:
            source.addProperty("App::PropertyString", "Variant")
            source.Variant = "Original"
            source.setPropertyStatus("Variant", "CopyOnChange")
        array = document.addObject("App::Link", "Fasteners")
        array.LinkedObject = source
        if assigned_material:
            array.addProperty("App::PropertyString", "Material", "Modelryx")
            array.Material = "Steel"
            array.addProperty("App::PropertyFloat", "DensityKgM3", "Modelryx")
            array.DensityKgM3 = 7850
        if copy_on_change:
            array.LinkCopyOnChange = "Enabled"
            array.Variant = "Modified"
        if variant:
            array.addProperty("App::PropertyString", "Configuration", "Design")
            array.Configuration = "Option A"
        array.ElementCount = 3
        array.ShowElement = expanded
        if suppressed_array:
            array.addExtension("App::SuppressibleExtensionPython")
            array.Suppressed = True
        if expanded:
            document.recompute()
            for index, element in enumerate(array.ElementList):
                element.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(index * 10, 0, 0), FreeCAD.Rotation()
                )
            array.ElementList[1].ScaleVector = FreeCAD.Vector(2, 1, 0.5)
        else:
            array.PlacementList = [
                FreeCAD.Placement(FreeCAD.Vector(index * 10, 0, 0), FreeCAD.Rotation())
                for index in range(3)
            ]
            array.ScaleList = [
                FreeCAD.Vector(1, 1, 1),
                FreeCAD.Vector(2, 1, 0.5),
                FreeCAD.Vector(1, 1, 1),
            ]
        array.setElementVisible("1", False)
        if overlap_active:
            assert expanded
            array.ElementList[1].Placement = array.ElementList[0].Placement
            array.ElementList[1].ScaleVector = FreeCAD.Vector(1, 1, 1)
            array.setElementVisible("1", True)
        if extra_link:
            link = document.addObject("App::Link", "RegularLink")
            link.LinkedObject = source
            link.Placement.Base = FreeCAD.Vector(40, 0, 0)
        if transformed:
            parent = document.addObject(
                "App::FeaturePython" if unknown_parent else "App::Part", "ParentAssembly"
            )
            if unknown_parent:
                parent.addProperty("App::PropertyPlacement", "Placement")
                parent.addProperty("App::PropertyLinkList", "Group")
            parent.Placement = FreeCAD.Placement(
                FreeCAD.Vector(300, 40, 0), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 180)
            )
            if unknown_parent:
                parent.Group = [array]
            else:
                parent.addObject(array)
            array.Scale = 2
            array.Placement = FreeCAD.Placement(
                FreeCAD.Vector(100, 0, 0), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 90)
            )
        document.recompute()
        if suppressed_element:
            array.ElementList[2].addExtension("App::SuppressibleExtensionPython")
            array.ElementList[2].Suppressed = True
            document.recompute()
            assert array.ElementList[2].Suppressed is True
        if explicit_element_states:
            for index, element in enumerate(array.ElementList):
                element.addExtension("App::SuppressibleExtensionPython")
                element.Suppressed = all_elements_suppressed or index == 2
            document.recompute()
        if collapse_after_suppression:
            assert expanded and suppressed_element
            array.ShowElement = False
            document.recompute()
            assert not array.ElementList
        if unrelated_selector:
            other = document.addObject("App::FeaturePython", "OtherConfiguration")
            other.addProperty("App::PropertyString", "Variant")
            other.Variant = "Independent"
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_independent_link_target_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("IndependentLinkTarget")
    try:
        source = document.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(1, 2, 3)
        link = document.addObject("App::Link", "Instance")
        link.LinkedObject = source
        link.Placement.Base = FreeCAD.Vector(10, 0, 0)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_regular_link_group_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("RegularLinkGroup")
    try:
        source = document.addObject("Part::Feature", "GroupSource")
        source.Shape = Part.makeBox(1, 1, 1)
        group = document.addObject("App::LinkGroup", "UserGroup")
        group.ElementList = [source]
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_hidden_link_fixture(
    path: Path,
    *,
    hidden_parent: bool = False,
    suppressed_link: bool = False,
    suppressed_parent: bool = False,
    unverified_suppression: bool = False,
) -> None:
    document = FreeCAD.newDocument("HiddenLinkAssembly")
    try:
        source = document.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(1, 2, 3)
        assembly = document.addObject("App::Part", "Assembly")
        if suppressed_parent:
            assembly.addExtension("App::SuppressibleExtensionPython")
            assembly.Suppressed = True
        for index in range(2):
            link = document.addObject("App::Link", f"Link{index}")
            link.LinkedObject = source
            if index == 1 and suppressed_link:
                link.addExtension("App::SuppressibleExtensionPython")
                link.Suppressed = True
            if index == 1 and unverified_suppression:
                link.addProperty("App::PropertyBool", "Suppressed", "Custom")
                link.Suppressed = True
            link.Placement.Base = FreeCAD.Vector(index * 10, 0, 0)
            assembly.addObject(link)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(
            "GuiDocument.xml",
            "<Document><ViewProviderData>"
            '<ViewProvider name="Source"><Properties><Property name="Visibility"><Bool value="false"/></Property></Properties></ViewProvider>'
            '<ViewProvider name="Assembly"><Properties><Property name="Visibility"><Bool value="'
            + ("false" if hidden_parent else "true")
            + '"/></Property></Properties></ViewProvider>'
            '<ViewProvider name="Link0"><Properties><Property name="Visibility"><Bool value="true"/></Property></Properties></ViewProvider>'
            '<ViewProvider name="Link1"><Properties><Property name="Visibility"><Bool value="false"/></Property></Properties></ViewProvider>'
            "</ViewProviderData></Document>",
        )


def make_shared_array_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("SharedArray")
    try:
        source = document.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(1, 2, 3)
        array = document.addObject("App::Link", "Array")
        array.LinkedObject = source
        array.ElementCount = 3
        array.ShowElement = False
        for name in ("FirstGroup", "SecondGroup"):
            group = document.addObject("App::FeaturePython", name)
            group.addProperty("App::PropertyLinkList", "Group")
            group.Group = [array]
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_unresolved_array_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("UnresolvedArray")
    try:
        source = document.addObject("Part::Feature", "UnrelatedPart")
        source.Shape = Part.makeBox(1, 2, 3)
        array = document.addObject("App::Link", "MissingArray")
        array.ElementCount = 3
        array.ShowElement = False
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_same_basename_assembly(
    root: Path, *, outside_root: bool = False, arrays: bool = False
) -> Path:
    documents = []
    try:
        for index, folder in enumerate(("first", "second"), start=1):
            directory = root / folder
            directory.mkdir()
            document = FreeCAD.newDocument(f"Component{index}")
            documents.append(document)
            shape = document.addObject("Part::Feature", "Body")
            shape.Shape = Part.makeBox(index, 2, 3)
            document.recompute()
            document.saveAs(str(directory / "component.FCStd"))
        assembly = FreeCAD.newDocument("SameBasenameAssembly")
        documents.append(assembly)
        if outside_root:
            (root / "assembly").mkdir()
        path = root / ("assembly/same-basename.FCStd" if outside_root else "same-basename.FCStd")
        assembly.saveAs(str(path))
        group = assembly.addObject("App::Part", "Assembly")
        for index, component in enumerate(documents[:2]):
            link = assembly.addObject("App::Link", f"Link{index}")
            link.LinkedObject = component.getObject("Body")
            if arrays:
                link.ElementCount = 2
                link.ShowElement = False
            link.Placement.Base = FreeCAD.Vector(index * 10, 0, 0)
            group.addObject(link)
        assembly.recompute()
        assembly.saveAs(str(path))
        return path
    finally:
        for document in reversed(documents):
            FreeCAD.closeDocument(document.Name)


def make_claimed_children_fixture(path: Path) -> None:
    """Model a FeaturePython tree whose GUI children come from object links."""
    document = FreeCAD.newDocument("ClaimedChildren")
    try:
        source = document.addObject("Part::Feature", "Source")
        source.Label = "Source solid"
        source.Shape = Part.makeBox(10, 10, 10)
        operation = document.addObject("Part::FeaturePython", "Operation")
        operation.Label = "Shared operation"
        operation.Shape = source.Shape
        operation.addProperty("App::PropertyLink", "Base")
        operation.Base = source
        exploded = document.addObject("App::DocumentObjectGroup", "Exploded")
        exploded.Label = "Exploded result"
        for index, x_position in enumerate((0, 20)):
            child = document.addObject("Part::FeaturePython", f"Result{index}")
            child.Label = f"Result {index}"
            child.Shape = source.Shape
            child.Placement.Base = FreeCAD.Vector(x_position, 0, 0)
            child.addProperty("App::PropertyLink", "Base")
            child.Base = operation
            exploded.addObject(child)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_construction_only_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("ConstructionOnly")
    try:
        datum = document.addObject("App::FeaturePython", "DatumReference")
        datum.Label = "Construction datum"
        datum.addProperty("App::PropertyString", "Description")
        datum.Description = "Reference-only document"
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_partially_broken_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("PartiallyBroken")
    try:
        valid = document.addObject("Part::Feature", "ValidResult")
        valid.Shape = Part.makeBox(8, 6, 4)
        document.addObject("PartDesign::Feature", "BrokenFeature")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_broken_expression_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("BrokenExpression")
    try:
        document.addObject("Part::Box", "Source")
        box = document.addObject("Part::Box", "Box")
        box.setExpression("Length", "Source.Length")
        document.recompute()
        document.removeObject("Source")
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)


def make_face_color_fixture(path: Path) -> None:
    document = FreeCAD.newDocument("FaceColors")
    try:
        block = document.addObject("Part::Feature", "ColorBlock")
        block.Shape = Part.makeBox(10, 10, 10)
        document.recompute()
        document.saveAs(str(path))
    finally:
        FreeCAD.closeDocument(document.Name)
    face_colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
    ]
    material_file = "ColorBlock.ShapeAppearance.bin"
    material_data = len(face_colors).to_bytes(4, "little") + b"".join(
        bytes((0, 0, 0, 0)) + bytes((255, blue, green, red)) for red, green, blue in face_colors
    )
    with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(material_file, material_data)
        archive.writestr(
            "GuiDocument.xml",
            '<?xml version="1.0" encoding="UTF-8"?><Document>'
            '<ViewProvider name="ColorBlock"><Properties>'
            '<Property name="ShapeAppearance" type="App::PropertyMaterialList">'
            f'<MaterialList file="{material_file}"/>'
            "</Property></Properties></ViewProvider></Document>",
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="modelryx-golden-") as directory:
        root = Path(directory)
        reference_doc = FreeCAD.newDocument("DimensionReferenceValidation")
        try:
            reference_object = reference_doc.addObject("Part::Feature", "Box")
            reference_object.Shape = Part.makeBox(10, 10, 10)
            reference_doc.recompute()
            assert _valid_3d_references([(reference_object, ("Face1",))])
            assert not _valid_3d_references([(reference_object, ("Face999",))])
        finally:
            FreeCAD.closeDocument(reference_doc.Name)
        source = root / "golden-assembly.FCStd"
        package_path = root / "golden-assembly.pfg"
        make_fixture(source)
        convert_fcstd_to_modelryx_package(str(source), str(package_path))
        with zipfile.ZipFile(package_path) as package:
            manifest = json.loads(package.read("manifest.json"))
            bom = json.loads(package.read("model-preview.json"))
            diagnostics = json.loads(package.read("diagnostics.json"))
            glb_path = root / "model.glb"
            glb_path.write_bytes(package.read("model.glb"))
        assert manifest["source"]["sha256"]
        assert manifest["source"]["document"]["format"] == "FCStd"
        assert manifest["source"]["document"]["file_format_version"]
        assert manifest["source"]["document"]["document_schema_version"]
        assert manifest["source"]["document"]["authoring_freecad_version"]
        assert manifest["outputs"]["modelSha256"]
        assert manifest["converter"]["tessellationProfile"] == "standard"
        assert manifest["converter"]["tessellationSource"] == "profile"
        assert diagnostics["statistics"]["meshes"] == 1
        assert diagnostics["statistics"]["triangles"] == 24
        raw_metadata = json.loads(
            package_path.with_suffix(".freecad-raw-metadata.json").read_text()
        )
        part_metadata = json.loads(package_path.with_suffix(".part-metadata.json").read_text())
        validate_part_metadata(part_metadata)
        metadata_only_path = root / "metadata-only.json"
        tessellation_functions = (
            "mesh_for_shape",
            "mesh_for_colored_faces",
            "mesh_for_edges_and_points",
            "topological_edge_segments",
        )
        originals = {name: getattr(converter, name) for name in tessellation_functions}

        def reject_tessellation(*_args, **_kwargs):
            raise AssertionError("Metadata-only extraction invoked tessellation")

        try:
            for name in tessellation_functions:
                setattr(converter, name, reject_tessellation)
            extract_fcstd_part_metadata(str(source), str(metadata_only_path))
        finally:
            for name, value in originals.items():
                setattr(converter, name, value)
        metadata_only = json.loads(metadata_only_path.read_text())
        part_metadata["source"].pop("extracted_at")
        metadata_only["source"].pop("extracted_at")
        assert metadata_only == part_metadata
        assert raw_metadata["document_label"] == "golden-assembly"
        assert raw_metadata["source_document"] == manifest["source"]["document"]
        assert part_metadata["native"]["source_document"] == manifest["source"]["document"]
        assert raw_metadata["native"]["assembly_model"] == "APP_PART"
        assert part_metadata["native"]["assembly_model"] == "APP_PART"
        assert part_metadata["native"]["workbench_requirements"] == []
        assert part_metadata["schema"] == "modelryx.part-metadata"
        assert part_metadata["native"]["extraction_diagnostics"] == []
        assert part_metadata["extraction"]["sections"]["geometry"] == {
            "status": "complete",
            "records_found": 2,
            "records_skipped": 0,
            "method": "native_shape",
            "confidence": 1.0,
        }
        assert part_metadata["extraction"]["sections"]["drawings"]["status"] == ("not_applicable")
        section_confidence = part_metadata["extraction"]["provenance"]["section_confidence"]
        applicable = [
            section_confidence[name]
            for name, evidence in part_metadata["extraction"]["sections"].items()
            if evidence["status"] != "not_applicable"
        ]
        assert part_metadata["extraction"]["provenance"]["confidence"] == round(
            sum(applicable) / len(applicable), 3
        )
        assert section_confidence["geometry"] == 1.0
        assert diagnostics["sectionDiagnostics"] == []
        assert diagnostics["sections"] == part_metadata["extraction"]["sections"]
        assert diagnostics["statistics"]["durationMs"] >= 0

        parameter_source = root / "parameter-fixture.FCStd"
        parameter_metadata_path = root / "parameter-fixture.json"
        make_parameter_fixture(parameter_source)
        extract_fcstd_part_metadata(str(parameter_source), str(parameter_metadata_path))
        parameter_metadata = json.loads(parameter_metadata_path.read_text())
        validate_part_metadata(parameter_metadata)
        parameters = {item["name"]: item for item in parameter_metadata["parameters"]}
        assert parameters["ConfiguredBlock.DrivenLength"]["expression"] == ("Parameters.Thickness")
        assert parameters["ConfiguredBlock.DrivenLength"]["value"] == 12.5
        assert parameters["Thickness"]["native_name"] == "Parameters.A1"
        assert parameters["OverallLength"]["value"] == 25.0
        assert parameters["ConfiguredBlock.Configuration"]["is_user"] is True
        assert parameters["ConfiguredBlock.Configuration"]["value"] is None
        parameter_graph = parameter_metadata["native"]["parameter_graph"]
        assert parameter_graph["edges"] == [
            {
                "source": "Parameters.A1",
                "target": "ConfiguredBlock.DrivenLength",
                "reference": "Parameters.Thickness",
            },
            {
                "source": "Parameters.A1",
                "target": "Parameters.A2",
                "reference": "Thickness",
            },
        ]
        assert parameter_graph["key_parameters"] == ["Parameters.A1"]
        assert parameter_graph["driven_parameters"] == [
            "ConfiguredBlock.DrivenLength",
            "Parameters.A2",
        ]
        assert parameter_graph["cycles"] == []
        assert parameter_metadata["properties"]["configuration"] == "Production"
        assert parameter_metadata["native"]["spreadsheets"][0]["cells"][0]["alias"] == ("Thickness")
        configurations = parameter_metadata["native"]["configurations"]
        assert configurations["active"] is None
        assert configurations["active_values"] == ["Machined", "Production"]
        assert configurations["variants"] == [
            "Default",
            "Lightweight",
            "Machined",
            "Production",
        ]
        selectors = {item["object"]: item for item in configurations["selectors"]}
        assert selectors["DesignVariants"] == {
            "object": "DesignVariants",
            "label": "DesignVariants",
            "property": "Configuration",
            "active": "Machined",
            "options": ["Default", "Lightweight", "Machined"],
            "source_type": "App::VarSet",
        }
        assert selectors["ConfiguredBlock"]["active"] == "Production"
        assert parameter_metadata["extraction"]["capabilities"]["configurations"] == "complete"
        assert parameter_metadata["extraction"]["sections"]["parameters"] == {
            "status": "complete",
            "records_found": 5,
            "records_skipped": 0,
            "method": "native_properties_and_spreadsheets",
            "confidence": 1.0,
        }

        label_source = root / "labeled-expression.FCStd"
        label_output = root / "labeled-expression.json"
        make_labeled_expression_fixture(label_source)
        extract_fcstd_part_metadata(str(label_source), str(label_output))
        label_metadata = json.loads(label_output.read_text())
        validate_part_metadata(label_metadata)
        assert label_metadata["native"]["parameter_graph"]["edges"] == [
            {
                "source": "DesignSource.Thickness",
                "target": "Box.Width",
                "reference": "<<Design Plate>>.Thickness",
            }
        ]
        assert label_metadata["extraction"]["capabilities"]["parameter_dependencies"] == "complete"

        sketch_source = root / "sketch-semantics.FCStd"
        sketch_metadata_path = root / "sketch-semantics.json"
        make_sketch_fixture(sketch_source)
        extract_fcstd_part_metadata(str(sketch_source), str(sketch_metadata_path))
        sketch_metadata = json.loads(sketch_metadata_path.read_text())
        validate_part_metadata(sketch_metadata)
        sketches = sketch_metadata["native"]["sketches"]
        assert len(sketches) == 1
        assert sketches[0]["name"] == "DesignSketch"
        assert sketches[0]["geometry_count"] == 2
        assert sketches[0]["construction_geometry_count"] == 1
        assert sketches[0]["constraint_count"] == 3
        assert (
            next(item for item in sketches[0]["constraints"] if item.get("name") == "LengthDrive")[
                "expression"
            ]
            == "Parameters.Length"
        )
        sketch_distance = next(
            item for item in sketches[0]["constraints"] if item.get("name") == "LengthDrive"
        )
        assert sketch_distance["value"] == 10
        assert sketch_distance["unit"] == "mm"
        assert sketch_distance["driving"] is True
        assert sketch_distance["active"] is True
        radius_constraint = next(item for item in sketches[0]["constraints"] if item["type"] == "Radius")
        assert radius_constraint["driving"] is True and radius_constraint["active"] is True
        status_source = root / "sketch-constraint-states.FCStd"
        status_metadata_path = root / "sketch-constraint-states.json"
        make_sketch_constraint_state_fixture(status_source)
        extract_fcstd_part_metadata(str(status_source), str(status_metadata_path))
        status_metadata = json.loads(status_metadata_path.read_text())
        validate_part_metadata(status_metadata)
        constraints = status_metadata["native"]["sketches"][0]["constraints"]
        assert constraints[0]["driving"] is False and constraints[0]["active"] is True
        assert constraints[1]["driving"] is True and constraints[1]["active"] is False
        assert any(
            edge["source"] == "Parameters.A1"
            and edge["target"] == "DesignSketch.Constraints.LengthDrive"
            for edge in sketch_metadata["native"]["parameter_graph"]["edges"]
        )
        driven_constraint = next(
            item
            for item in sketch_metadata["parameters"]
            if item["name"] == "DesignSketch.Constraints.LengthDrive"
        )
        assert driven_constraint["value"] == 10
        assert driven_constraint["unit"] == "mm"
        assert driven_constraint["is_key"] is False
        assert any(
            edge["source"] == "DesignSketch.Constraints[2]"
            and edge["target"] == "DrivenTarget.RadiusValue"
            for edge in sketch_metadata["native"]["parameter_graph"]["edges"]
        )
        assert {item["type"] for item in sketches[0]["constraints"]} == {
            "Distance",
            "Horizontal",
            "Radius",
        }
        assert sketch_metadata["extraction"]["sections"]["sketches"] == {
            "status": "partial",
            "records_found": 1,
            "records_skipped": 0,
            "method": "native_sketcher",
            "confidence": 0.65,
        }
        assert sketch_metadata["extraction"]["capabilities"]["sketch_constraints"] == "partial"
        assert (
            sketch_metadata["extraction"]["capabilities"]["sketch_degrees_of_freedom"]
            == "unavailable"
        )

        angle_source = root / "angular-sketch.FCStd"
        angle_output = root / "angular-sketch.json"
        make_angular_sketch_fixture(angle_source)
        extract_fcstd_part_metadata(str(angle_source), str(angle_output))
        angular_sketch = json.loads(angle_output.read_text())
        validate_part_metadata(angular_sketch)
        angle_constraint = angular_sketch["native"]["sketches"][0]["constraints"][0]
        assert angle_constraint["type"] == "Angle"
        assert angle_constraint["value"] == 45
        assert angle_constraint["unit"] == "deg"

        attached_source = root / "attached-sketch.FCStd"
        attached_metadata_path = root / "attached-sketch.json"
        make_attached_sketch_fixture(attached_source)
        extract_fcstd_part_metadata(str(attached_source), str(attached_metadata_path))
        attached_metadata = json.loads(attached_metadata_path.read_text())
        validate_part_metadata(attached_metadata)
        attached = attached_metadata["native"]["sketches"][0]
        assert attached["support"]["object"] == "SupportBox"
        assert attached["support"]["subelements"] == ["Face1"]
        assert attached["attachment_status"] == "supported"
        assert attached["attachment_offset_placement"]["position_mm"] == [0, 0, 2]
        assert attached["attachment_offset_placement"]["quaternion_xyzw"] == [0, 0, 0, 1]
        assert attached["supports"] == [attached["support"]]
        assert attached["external_geometry_references"][0]["object"] == "SupportBox"
        assert attached["external_geometry_references"][0]["subelements"] == ["Edge1", "Edge2"]
        assert attached["external_geometry_count"] == 2
        assert {
            (item["role"], item["subelement"]) for item in attached["indexed_topology_references"]
        } == {
            ("attachment_support", "Face1"),
            ("external_geometry", "Edge1"),
            ("external_geometry", "Edge2"),
        }
        external_constraint = next(
            item for item in attached["constraints"] if item["type"] == "PointOnObject"
        )
        assert external_constraint["second_geometry"] == -4
        assert attached["constraints"][0]["virtual_space"] is False
        assert attached["conflicting_constraints"] == []
        assert attached["fully_constrained"] is False
        assert attached["solver_state"] == "under_constrained"

        invalid_source = root / "invalid-sketch.FCStd"
        invalid_metadata_path = root / "invalid-sketch.json"
        make_invalid_sketch_fixture(invalid_source)
        extract_fcstd_part_metadata(str(invalid_source), str(invalid_metadata_path))
        invalid_metadata = json.loads(invalid_metadata_path.read_text())
        validate_part_metadata(invalid_metadata)
        invalid = invalid_metadata["native"]["sketches"][0]
        assert invalid["solver_state"] == "error"
        assert invalid["fully_constrained"] is None
        assert invalid_metadata["extraction"]["capabilities"]["sketch_constraints"] == "partial"

        missing_support_source = root / "missing-sketch-support.FCStd"
        missing_support_metadata = root / "missing-sketch-support.json"
        make_unresolved_sketch_support_fixture(missing_support_source)
        extract_fcstd_part_metadata(str(missing_support_source), str(missing_support_metadata))
        support_metadata = json.loads(missing_support_metadata.read_text())
        validate_part_metadata(support_metadata)
        assert (
            support_metadata["native"]["sketches"][0]["attachment_status"]
            == "support_not_specified"
        )
        assert support_metadata["extraction"]["sections"]["sketches"]["status"] == "partial"
        assert any(
            issue["code"] == "SKETCH_ATTACHMENT_SUPPORT_UNAVAILABLE"
            for issue in support_metadata["native"]["extraction_diagnostics"]
        )

        pocket_source = root / "pocket-construction.FCStd"
        pocket_output = root / "pocket-construction.json"
        make_pocket_construction_fixture(pocket_source)
        extract_fcstd_part_metadata(str(pocket_source), str(pocket_output))
        pocket_metadata = json.loads(pocket_output.read_text())
        validate_part_metadata(pocket_metadata)
        assert pocket_metadata["features"]["summary"]["hole_count"] == 1
        assert [
            (hole["diameter_mm"], hole["count"]) for hole in pocket_metadata["features"]["holes"]
        ] == [(4.0, 1)]

        after_tip_source = root / "feature-after-tip.FCStd"
        after_tip_output = root / "feature-after-tip.json"
        make_feature_after_tip_fixture(after_tip_source)
        extract_fcstd_part_metadata(str(after_tip_source), str(after_tip_output))
        after_tip_metadata = json.loads(after_tip_output.read_text())
        validate_part_metadata(after_tip_metadata)
        assert after_tip_metadata["features"]["summary"]["pocket_count"] == 0
        inactive = next(
            item
            for item in after_tip_metadata["features"]["items"]
            if item["persistent_id"] == "FutureCut"
        )
        assert inactive["type"] == "POCKET"
        assert inactive["properties"]["active_in_body"] is False
        assert any(
            "FutureCut" in item["features"]
            for item in after_tip_metadata["native"]["document_health"]["features_after_body_tip"]
        )

        missing_tip_source = root / "feature-no-tip.FCStd"
        missing_tip_output = root / "feature-no-tip.json"
        make_feature_after_tip_fixture(missing_tip_source, no_tip=True)
        extract_fcstd_part_metadata(str(missing_tip_source), str(missing_tip_output))
        missing_tip = json.loads(missing_tip_output.read_text())
        validate_part_metadata(missing_tip)
        assert missing_tip["features"]["summary"] == {}
        assert missing_tip["quality"]["features"] == "partial"
        assert missing_tip["native"]["document_health"]["bodies_with_invalid_tips"] == ["Body"]
        assert any(
            item["persistent_id"] == "FutureCut" for item in missing_tip["features"]["items"]
        )

        missing_profile_source = root / "missing-profile.FCStd"
        missing_profile_output = root / "missing-profile.json"
        make_missing_profile_fixture(missing_profile_source)
        extract_fcstd_part_metadata(str(missing_profile_source), str(missing_profile_output))
        missing_profile = json.loads(missing_profile_output.read_text())
        validate_part_metadata(missing_profile)
        assert missing_profile["features"]["summary"] == {}
        assert missing_profile["quality"]["features"] == "partial"
        assert any(
            item["persistent_id"] == "BrokenPad" for item in missing_profile["features"]["items"]
        )
        assert {
            item["section"]
            for item in missing_profile["native"]["extraction_diagnostics"]
            if item["code"] == "MISSING_FEATURE_SUPPORT"
        } == {"features", "geometry"}

        annular_source = root / "annular-pocket.FCStd"
        annular_output = root / "annular-pocket.json"
        make_pocket_construction_fixture(annular_source, annular=True)
        extract_fcstd_part_metadata(str(annular_source), str(annular_output))
        annular = json.loads(annular_output.read_text())
        validate_part_metadata(annular)
        assert annular["features"]["summary"]["hole_count"] == 0
        assert annular["features"]["summary"]["pocket_count"] == 1
        assert not annular["features"].get("holes")
        assert annular["quality"]["features"] == "partial"
        assert any(
            item["code"] == "POCKET_PROFILE_TOPOLOGY_AMBIGUOUS"
            for item in annular["native"]["extraction_diagnostics"]
        )

        feature_source = root / "feature-semantics.FCStd"
        feature_metadata_path = root / "feature-semantics.json"
        property_document = FreeCAD.newDocument("NonFiniteProperties")
        try:
            property_object = property_document.addObject("App::FeaturePython", "Properties")
            property_object.addProperty("App::PropertyFloat", "InvalidNumber")
            property_object.InvalidNumber = float("nan")
            assert "InvalidNumber" not in converter.custom_properties(property_object)
            assert converter.primitive_property([1.0, float("inf")]) is None
            assert converter.primitive_property([1.0, 2.0]) == [1.0, 2.0]
            json.dumps(converter.custom_properties(property_object), allow_nan=False)
        finally:
            FreeCAD.closeDocument(property_document.Name)
        make_feature_semantics_fixture(feature_source)
        extract_fcstd_part_metadata(str(feature_source), str(feature_metadata_path))
        feature_metadata = json.loads(feature_metadata_path.read_text())
        validate_part_metadata(feature_metadata)
        original_feature_properties = converter.normalized_feature_properties
        def unreadable_feature(obj):
            if obj.Name == "NativeBox":
                raise RuntimeError("unreadable feature property")
            return original_feature_properties(obj)
        try:
            converter.normalized_feature_properties = unreadable_feature
            partial_feature_path = root / "feature-semantics-partial.json"
            extract_fcstd_part_metadata(str(feature_source), str(partial_feature_path))
        finally:
            converter.normalized_feature_properties = original_feature_properties
        partial_features = json.loads(partial_feature_path.read_text())
        validate_part_metadata(partial_features)
        assert partial_features["quality"]["features"] == "partial"
        assert any(issue["code"] == "FEATURE_PROPERTIES_EXTRACTION_FAILED"
                   and issue.get("object_name") == "NativeBox"
                   for issue in partial_features["native"]["extraction_diagnostics"])
        assert any(item["persistent_id"] == "NativeCylinder" and "radius_mm" in item["properties"]
                   for item in partial_features["features"]["items"])
        feature_items = {
            item["persistent_id"]: item for item in feature_metadata["features"]["items"]
        }
        box = feature_items["NativeBox"]
        assert box["type"] == "BOSS"
        assert box["properties"]["length_mm"] == 30.0
        assert box["properties"]["width_mm"] == 12.0
        assert box["properties"]["height_mm"] == 6.0
        cylinder = feature_items["NativeCylinder"]
        assert cylinder["type"] == "BOSS"
        assert cylinder["properties"]["radius_mm"] == 4.0
        assert cylinder["properties"]["height_mm"] == 15.0
        assert cylinder["properties"]["angle_deg"] == 180.0
        cone = feature_items["NativeCone"]
        assert cone["type"] == "BOSS"
        assert cone["properties"]["radius1_mm"] == 6.0
        assert cone["properties"]["radius2_mm"] == 2.0
        assert cone["properties"]["height_mm"] == 18.0
        sphere = feature_items["NativeSphere"]
        assert sphere["type"] == "BOSS"
        assert sphere["properties"]["radius_mm"] == 7.0
        assert sphere["properties"]["angle1_deg"] == -45.0
        assert sphere["properties"]["angle2_deg"] == 60.0
        assert sphere["properties"]["angle3_deg"] == 240.0
        torus = feature_items["NativeTorus"]
        assert torus["type"] == "BOSS"
        assert torus["properties"]["major_radius_mm"] == 10.0
        assert torus["properties"]["minor_radius_mm"] == 3.0
        assert torus["properties"]["angle3_deg"] == 300.0
        prism = feature_items["NativePrism"]
        assert prism["type"] == "BOSS"
        assert prism["properties"]["side_count"] == 6.0
        assert prism["properties"]["circumradius_mm"] == 5.0
        assert prism["properties"]["height_mm"] == 11.0
        assert prism["properties"]["first_angle_deg"] == 15.0
        assert prism["properties"]["second_angle_deg"] == 75.0
        wedge = feature_items["NativeWedge"]
        assert wedge["type"] == "BOSS"
        assert wedge["properties"]["x_max_mm"] == 12.0
        assert wedge["properties"]["x2_min_mm"] == 2.0
        assert wedge["properties"]["z2_max_mm"] == 5.0
        ellipsoid = feature_items["NativeEllipsoid"]
        assert ellipsoid["type"] == "BOSS"
        assert ellipsoid["properties"]["radius1_mm"] == 8.0
        assert ellipsoid["properties"]["radius2_mm"] == 6.0
        assert ellipsoid["properties"]["radius3_mm"] == 4.0
        assert ellipsoid["properties"]["angle3_deg"] == 270.0
        feature_items_by_name = {
            item["name"]: item for item in feature_metadata["features"]["items"]
        }
        orthogonal_array = feature_items_by_name["Orthogonal Array"]
        assert orthogonal_array["type"] == "PATTERN"
        assert orthogonal_array["properties"]["pattern_kind"] == "ortho"
        assert orthogonal_array["properties"]["count_x"] == 2
        assert orthogonal_array["properties"]["count_y"] == 3
        assert orthogonal_array["properties"]["count_z"] == 1
        assert orthogonal_array["properties"]["occurrence_count"] == 6
        assert orthogonal_array["properties"]["interval_x_mm"] == [10.0, 0.0, 0.0]
        assert orthogonal_array["properties"]["interval_y_mm"] == [0.0, 12.0, 0.0]
        polar_array = feature_items_by_name["Polar Array"]
        assert polar_array["type"] == "PATTERN"
        assert polar_array["properties"]["pattern_kind"] == "polar"
        assert polar_array["properties"]["occurrence_count"] == 4
        assert polar_array["properties"]["angle_deg"] == 180.0
        assert polar_array["properties"]["axis"] == [0.0, 0.0, 1.0]
        circular_array = feature_items_by_name["Circular Array"]
        assert circular_array["type"] == "PATTERN"
        assert circular_array["properties"]["pattern_kind"] == "circular"
        assert circular_array["properties"]["occurrence_count"] == 5
        assert circular_array["properties"]["circle_count"] == 2
        assert circular_array["properties"]["polar_count"] == 5
        assert circular_array["properties"]["symmetry"] == 1
        assert circular_array["properties"]["radial_distance_mm"] == 15.0
        assert circular_array["properties"]["tangential_distance_mm"] == 20.0
        assert circular_array["properties"]["axis"] == [0.0, 1.0, 0.0]
        assert circular_array["properties"]["center_mm"] == [4.0, 5.0, 6.0]
        path_array = feature_items_by_name["Path Array"]
        assert path_array["type"] == "PATTERN"
        assert path_array["properties"]["pattern_kind"] == "path"
        assert path_array["properties"]["occurrence_count"] == 5
        assert path_array["properties"]["align"] is True
        assert path_array["properties"]["align_mode"] == "Original"
        assert path_array["properties"]["start_offset_mm"] == 1.0
        assert path_array["properties"]["end_offset_mm"] == 2.0
        assert path_array["properties"]["spine_refs"] == ["ArrayPath"]
        point_array = feature_items_by_name["Point Array"]
        assert point_array["type"] == "PATTERN"
        assert point_array["properties"]["pattern_kind"] == "point"
        assert point_array["properties"]["occurrence_count"] == 3
        assert point_array["properties"]["point_refs"] == ["ArrayPoints"]
        extrusion = feature_items["NativeExtrusion"]
        assert extrusion["type"] == "EXTRUSION"
        assert extrusion["properties"]["base_refs"] == ["ExtrusionProfile"]
        assert extrusion["properties"]["direction"] == [0.0, 0.0, 8.0]
        assert extrusion["properties"]["length_forward_mm"] == 8.0
        boolean = feature_items["NativeCut"]
        assert boolean["type"] == "BOOLEAN"
        assert boolean["properties"]["base_refs"] == ["BooleanBase"]
        assert boolean["properties"]["tool_refs"] == ["BooleanTool"]
        loft = feature_items["NativeLoft"]
        assert loft["type"] == "LOFT"
        assert loft["properties"]["section_refs"] == ["LoftZLowerSection", "LoftAUpperSection"]
        assert loft["properties"]["solid"] is True
        assert loft["properties"]["ruled"] is True
        assert loft["properties"]["closed"] is False
        assert loft["properties"]["linearize"] is True
        assert loft["properties"]["max_degree"] == 3.0

        for fixture_name, joint_index, conflicting in (
            ("fixed", 0, False), ("revolute", 1, False), ("conflicting", 0, True),
        ):
            solver_source = root / f"solver-observation-{fixture_name}.FCStd"
            make_solver_observation_fixture(solver_source, joint_index, conflicting=conflicting)
            original_solver_source = hashlib.sha256(solver_source.read_bytes()).hexdigest()
            solver_document = FreeCAD.openDocument(str(solver_source))
            try:
                # A return of zero does not establish freedom or constraint satisfaction.
                solve_result = solver_document.getObject("Assembly").solve()
                assert isinstance(solve_result, int)
                if not conflicting:
                    assert solve_result == 0
                if conflicting and solve_result == 0:
                    base = solver_document.getObject("Base")
                    moving = solver_document.getObject("Moving")
                    first_joint = solver_document.getObject("Joint")
                    other_joint = solver_document.getObject("ConflictingJoint")
                    separations = []
                    for candidate in (first_joint, other_joint):
                        first = base.Placement.multiply(candidate.Placement1)
                        second = moving.Placement.multiply(candidate.Placement2)
                        separations.append((first.Base - second.Base).Length)
                    assert max(separations) > 9.9, separations
            finally:
                FreeCAD.closeDocument(solver_document.Name)
            solver_metadata_path = root / f"solver-observation-{fixture_name}.json"
            extract_fcstd_part_metadata(str(solver_source), str(solver_metadata_path))
            assert hashlib.sha256(solver_source.read_bytes()).hexdigest() == original_solver_source
            solver_metadata = json.loads(solver_metadata_path.read_text())
            validate_part_metadata(solver_metadata)
            assert solver_metadata["extraction"]["capabilities"]["assembly_solver"] == "unavailable"
            joint_record = next(
                item for item in solver_metadata["native"]["assembly_joints"]
                if item["name"] == "Joint"
            )
            assert joint_record["solver_status"] == "unavailable"
            assert joint_record["degrees_of_freedom_basis"] == "nominal_joint_type"
            assert any(
                issue["code"] == "ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE"
                for issue in solver_metadata["native"]["extraction_diagnostics"]
            )

        native_assembly_source = root / "native-assembly.FCStd"
        native_assembly_metadata_path = root / "native-assembly.json"
        make_native_assembly_fixture(native_assembly_source)
        extract_fcstd_part_metadata(str(native_assembly_source), str(native_assembly_metadata_path))
        native_assembly_metadata = json.loads(native_assembly_metadata_path.read_text())
        validate_part_metadata(native_assembly_metadata)
        assert any(
            issue["code"] == "ASSEMBLY_SOLVER_OBSERVATION_UNAVAILABLE"
            and issue.get("object_name") == "Assembly"
            for issue in native_assembly_metadata["native"]["extraction_diagnostics"]
        )
        joints = {
            item["name"]: item for item in native_assembly_metadata["native"]["assembly_joints"]
        }
        assert joints["Pivot"]["type"] == "Revolute"
        assert joints["Pivot"]["degrees_of_freedom"] == 1
        assert joints["Pivot"]["degrees_of_freedom_basis"] == "nominal_joint_type"
        assert joints["Pivot"]["solver_status"] == "unavailable"
        mapped = [ref["occurrence_resolution"] for ref in joints["LinkedPair"]["references"]]
        assert all(item["status"] == "resolved" for item in mapped), mapped
        assert mapped[0]["occurrence_id"] != mapped[1]["occurrence_id"]
        nested_mapping = joints["NestedPair"]["references"][0]["occurrence_resolution"]
        assert nested_mapping["status"] == "resolved", nested_mapping
        occurrence = next(
            item for item in native_assembly_metadata["native"]["occurrences"]
            if item["occurrenceId"] == nested_mapping["occurrence_id"]
        )
        assert occurrence["source"]["objectName"] == "NestedArm"
        assert "Joints" not in joints
        assert joints["Pivot"]["limits_enabled"]["angle_min"] is True
        assert [item["object"] for item in joints["Pivot"]["references"]] == [
            "BasePlate",
            "Arm",
        ]
        assert joints["Pivot"]["references"][0]["subelements"] == ["Face1"]
        assert joints["GroundBase"]["type"] == "Grounded"
        assert joints["GroundBase"]["grounded"] is True
        assert joints["FixedPair"]["grounded"] is False
        suppression_known = all("suppressed" in joint for joint in joints.values())
        for joint in joints.values():
            if "suppressed" in joint:
                assert joint["suppression_source"] == "native"
                assert isinstance(joint["suppressed"], bool)
            else:
                assert joint["suppression_source"] in {"not_exposed", "unverified", "unavailable"}
        if not suppression_known:
            assert any(
                issue["code"] == "ASSEMBLY_JOINT_SUPPRESSION_UNVERIFIED"
                for issue in native_assembly_metadata["native"]["extraction_diagnostics"]
            )
        incomplete_references = any(not joint["references_complete"] for joint in joints.values())
        unresolved_occurrences = any(
            ref["occurrence_resolution"]["status"] != "resolved"
            for joint in joints.values() for ref in joint["references"]
        )
        expected_constraints = (
            "partial" if not suppression_known or incomplete_references or unresolved_occurrences
            else "complete"
        )
        actual_constraints = native_assembly_metadata["extraction"]["capabilities"]["assembly_constraints"]
        assert actual_constraints == expected_constraints, {
            "expected": expected_constraints, "actual": actual_constraints,
            "joints": joints, "diagnostics": native_assembly_metadata["native"]["extraction_diagnostics"],
        }

        reference_source = root / "reference-geometry.FCStd"
        reference_metadata_path = root / "reference-geometry.json"
        make_reference_geometry_fixture(reference_source)
        extract_fcstd_part_metadata(str(reference_source), str(reference_metadata_path))
        reference_metadata = json.loads(reference_metadata_path.read_text())
        validate_part_metadata(reference_metadata)
        datums = {item["name"]: item for item in reference_metadata["native"]["reference_geometry"]}
        assert set(datums) == {"FixturePoint", "InspectionPlane", "MachiningLCS", "ToolAxis"}
        assert datums["InspectionPlane"]["kind"] == "plane"
        assert datums["InspectionPlane"]["placement"]["position_mm"] == [5.0, 2.0, 1.0]
        assert datums["ToolAxis"]["kind"] == "axis"
        assert datums["FixturePoint"]["kind"] == "point"
        assert datums["MachiningLCS"]["kind"] == "coordinate_system"
        assert datums["MachiningLCS"]["placement"]["position_mm"] == [10.0, 5.0, 0.0]
        assert reference_metadata["extraction"]["capabilities"]["reference_geometry"] == (
            "complete"
        )
        assert reference_metadata["quality"]["features"] == "complete"
        reference_feature_items = reference_metadata["features"]["items"]
        assert {item["type"] for item in reference_feature_items} == {"BODY", "BOSS"}
        assert {item["persistent_id"] for item in reference_feature_items}.isdisjoint(datums)
        assert part_metadata["identity"]["kind"] == "assembly"
        assert part_metadata["identity"]["part_number"] == "ASM-100"
        assert part_metadata["identity"]["revision"] == "B"
        assert part_metadata["identity"]["description"] == "Golden test assembly"
        assert part_metadata["identity"]["tags"] == ["golden", "fixture"]
        assert part_metadata["properties"]["PartNumber"] == "ASM-100"
        assert part_metadata["assembly"] == {
            "is_assembly": True,
            "instance_count": 2,
            "unique_component_count": 2,
            "subassembly_count": 1,
            "bom_row_count": 2,
        }
        assert len(part_metadata["native"]["bom_rows"]) == 2
        assert {row["quantity"] for row in part_metadata["native"]["bom_rows"]} == {1}
        occurrences = part_metadata["native"]["occurrences"]
        bracket_occurrences = [item for item in occurrences if item["name"].startswith("Bracket")]
        assert len(bracket_occurrences) == 2
        assert all(item["source"]["typeId"] == "Part::Feature" for item in bracket_occurrences)
        assert sorted(
            round(item["placement"]["position"][0], 3) for item in bracket_occurrences
        ) == [0.0, 0.03]
        relocated_directory = root / "relocated"
        relocated_directory.mkdir()
        relocated_source = relocated_directory / source.name
        relocated_package = relocated_directory / package_path.name
        shutil.copy2(source, relocated_source)
        convert_fcstd_to_modelryx_package(str(relocated_source), str(relocated_package))
        relocated_metadata = json.loads(
            relocated_package.with_suffix(".part-metadata.json").read_text()
        )
        assert [
            (item["partId"], item["occurrenceId"])
            for item in relocated_metadata["native"]["occurrences"]
        ] == [
            (item["partId"], item["occurrenceId"])
            for item in part_metadata["native"]["occurrences"]
        ]
        assert part_metadata["geometry"]["bounding_box"] == {
            "x_mm": 50.0,
            "y_mm": 10.0,
            "z_mm": 5.0,
        }
        assert part_metadata["geometry"]["shape"]["volume_mm3"] == 2000.0
        assert part_metadata["geometry"]["topology"]["solid_count"] == 2
        assert part_metadata["geometry"]["center"] == {
            "x_mm": 25.0,
            "y_mm": 5.0,
            "z_mm": 2.5,
        }
        assert part_metadata["quality"]["physical"] == "complete"
        assert part_metadata["physical"] == {
            "material": {"name": "Steel"},
            "density_kg_m3": 7850.0,
            "mass_kg": 0.0157,
            "center_of_mass": {"x_mm": 25.0, "y_mm": 5.0, "z_mm": 2.5},
        }
        partial_source = root / "partial-material.FCStd"
        partial_path = root / "partial-material.json"
        make_partial_material_fixture(partial_source)
        extract_fcstd_part_metadata(str(partial_source), str(partial_path))
        partial_metadata = json.loads(partial_path.read_text())
        validate_part_metadata(partial_metadata)
        assert "material" not in partial_metadata.get("physical", {})
        assert partial_metadata["quality"]["physical"] == "partial"
        density_source, density_path = root / "native-density.FCStd", root / "native-density.json"
        make_native_density_fixture(density_source)
        extract_fcstd_part_metadata(str(density_source), str(density_path))
        density_metadata = json.loads(density_path.read_text())
        validate_part_metadata(density_metadata)
        assert abs(density_metadata["physical"]["density_kg_m3"] - 7850) < 1e-6
        assert abs(density_metadata["physical"]["mass_kg"] - 0.00785) < 1e-9
        assembly_occurrence = next(
            item
            for item in raw_metadata["native"]["occurrences"]
            if item["name"] == "Golden Assembly"
        )
        assert assembly_occurrence["properties"]["centerOfMassMm"] == [25.0, 5.0, 2.5]
        assert part_metadata["quality"]["features"] == "unavailable"
        assert set(bom) == {"schemaVersion", "rootOccurrenceId", "items"}
        assert all("properties" not in item and "source" not in item for item in bom["items"])
        brackets = [item for item in bom["items"] if item["name"].startswith("Bracket")]
        assert len(brackets) == 2
        assert {item["parentOccurrenceId"] for item in brackets} == {
            next(item["occurrenceId"] for item in bom["items"] if item["name"] == "Golden Assembly")
        }
        assert brackets[0]["display"]["color"] == brackets[1]["display"]["color"]
        assert {item["cadType"] for item in brackets} == {"Feature"}
        assert sorted(round(item["placement"]["position"][0], 3) for item in brackets) == [
            0.0,
            0.03,
        ]
        scene = trimesh.load_scene(glb_path)
        assert len(scene.geometry) == 1
        assert len(scene.graph.nodes_geometry) == 2

        compound_source = root / "asymmetric-compound.FCStd"
        compound_metadata_path = root / "asymmetric-compound.json"
        make_asymmetric_compound_fixture(compound_source)
        extract_fcstd_part_metadata(str(compound_source), str(compound_metadata_path))
        compound_metadata = json.loads(compound_metadata_path.read_text())
        assert compound_metadata["geometry"]["center"] == {
            "x_mm": 20.0,
            "y_mm": 5.0,
            "z_mm": 5.0,
        }
        assert compound_metadata["physical"]["center_of_mass"] == {
            "x_mm": 21.666667,
            "y_mm": 5.0,
            "z_mm": 5.0,
        }

        limited_path = root / "limited.pfg"
        try:
            convert_fcstd_to_modelryx_package(str(source), str(limited_path), max_triangles=1)
        except ValueError as error:
            assert "triangle limit" in str(error)
        else:
            raise AssertionError("triangle limit did not reject the fixture")

        linked_source = make_external_link_fixture(root)
        linked_package = root / "linked-assembly.pfg"
        convert_fcstd_to_modelryx_package(str(linked_source), str(linked_package))
        with zipfile.ZipFile(linked_package) as package:
            linked_diagnostics = json.loads(package.read("diagnostics.json"))
            linked_glb = root / "linked.glb"
            linked_glb.write_bytes(package.read("model.glb"))
        linked_scene = trimesh.load_scene(linked_glb)
        linked_metadata = json.loads(linked_package.with_suffix(".part-metadata.json").read_text())
        assert linked_metadata["assembly"]["instance_count"] == 2
        assert linked_metadata["assembly"]["unique_component_count"] == 1
        assert linked_metadata["assembly"]["bom_row_count"] == 1
        assert len(linked_metadata["native"]["occurrences"]) >= 3
        dependencies = linked_metadata["native"]["dependencies"]
        assert isinstance(dependencies, list)
        linked_occurrences = [
            item
            for item in linked_metadata["native"]["occurrences"]
            if item.get("dependency", {}).get("external")
        ]
        assert len(linked_occurrences) == 2
        assert {item["configuration"] for item in linked_occurrences} == {"Long", "Short"}
        linked_hash = hashlib.sha256((root / "linked-component.FCStd").read_bytes()).hexdigest()
        assert all(
            item["source"]["linkedDocumentContentSha256"] == linked_hash
            and item["source"]["linkedDocumentHashBasis"] == "loaded_file_bytes"
            for item in linked_occurrences
        )
        dependency_section = linked_metadata["extraction"]["sections"]["dependencies"]
        assert dependency_section["records_found"] == len(dependencies)
        assert dependency_section["status"] == "partial"
        assert dependencies[0]["document"] == "linked-component.FCStd"

        same_names_source = make_same_basename_assembly(root)
        same_names_output = root / "same-basename.json"
        extract_fcstd_part_metadata(str(same_names_source), str(same_names_output))
        same_names_metadata = json.loads(same_names_output.read_text())
        validate_part_metadata(same_names_metadata)
        same_names = [
            item
            for item in same_names_metadata["native"]["occurrences"]
            if item.get("source", {}).get("externalDocument")
        ]
        assert len(same_names) == 2
        assert len({item["partId"] for item in same_names}) == 2
        assert {item["source"]["linkedDocument"] for item in same_names} == {
            "first/component.FCStd",
            "second/component.FCStd",
        }
        arrays_root = root / "same-basename-arrays"
        arrays_root.mkdir()
        arrays_source = make_same_basename_assembly(arrays_root, arrays=True)
        arrays_output = root / "same-basename-arrays.json"
        extract_fcstd_part_metadata(str(arrays_source), str(arrays_output))
        arrays_metadata = json.loads(arrays_output.read_text())
        validate_part_metadata(arrays_metadata)
        assert {array["target_document"] for array in arrays_metadata["native"]["link_arrays"]} == {
            "first/component.FCStd",
            "second/component.FCStd",
        }
        assert arrays_metadata["native"]["bom_rows"] == []
        assert "instance_count" not in arrays_metadata["assembly"]
        assert "unique_component_count" not in arrays_metadata["assembly"]
        assert "bom_row_count" not in arrays_metadata["assembly"]
        assert any(
            issue["code"] == "LINK_ARRAY_BOM_QUANTITY_UNAVAILABLE"
            for issue in arrays_metadata["native"]["extraction_diagnostics"]
        )
        missing_array_source = root / "missing-array.FCStd"
        missing_array_output = root / "missing-array.json"
        make_unresolved_array_fixture(missing_array_source)
        extract_fcstd_part_metadata(str(missing_array_source), str(missing_array_output))
        missing_array = json.loads(missing_array_output.read_text())
        validate_part_metadata(missing_array)
        assert "instance_count" not in missing_array["assembly"]
        assert any(
            issue["code"] == "LINK_ARRAY_BOM_QUANTITY_UNAVAILABLE"
            for issue in missing_array["native"]["extraction_diagnostics"]
        )
        shared_source = root / "shared-array.FCStd"
        make_shared_array_fixture(shared_source)
        bounded_package = root / "shared-array-bounded.pfg"
        convert_fcstd_to_modelryx_package(str(shared_source), str(bounded_package), max_nodes=5)
        bounded = json.loads(bounded_package.with_suffix(".part-metadata.json").read_text())
        assert (
            sum(
                len(element["world_transforms"])
                for array in bounded["native"]["link_arrays"]
                for element in array["elements"]
            )
            == 5
        )
        assert any(
            array.get("world_transforms_truncated") for array in bounded["native"]["link_arrays"]
        )
        assert any(
            issue["code"] == "LINK_ARRAY_WORLD_EVIDENCE_TRUNCATED"
            for issue in bounded["native"]["extraction_diagnostics"]
        )
        outside_arrays_root = root / "outside-basename-arrays"
        outside_arrays_root.mkdir()
        outside_arrays_source = make_same_basename_assembly(
            outside_arrays_root, outside_root=True, arrays=True
        )
        outside_arrays_output = root / "outside-basename-arrays.json"
        extract_fcstd_part_metadata(str(outside_arrays_source), str(outside_arrays_output))
        outside_arrays = json.loads(outside_arrays_output.read_text())
        validate_part_metadata(outside_arrays)
        targets = [array["target_document"] for array in outside_arrays["native"]["link_arrays"]]
        assert len(set(targets)) == 2
        assert all(
            target.startswith("external/") and target.endswith("/component.FCStd")
            for target in targets
        )
        assert str(outside_arrays_root) not in json.dumps(outside_arrays)
        assert any(
            issue["code"] == "EXTERNAL_DOCUMENT_IDENTITY_LOCAL"
            for issue in outside_arrays["native"]["extraction_diagnostics"]
        )
        outside_root = root / "outside-root"
        outside_root.mkdir()
        outside_source = make_same_basename_assembly(outside_root, outside_root=True)
        outside_output = root / "outside-root.json"
        extract_fcstd_part_metadata(str(outside_source), str(outside_output))
        outside_metadata = json.loads(outside_output.read_text())
        validate_part_metadata(outside_metadata)
        outside_items = [
            item
            for item in outside_metadata["native"]["occurrences"]
            if item.get("source", {}).get("externalDocument")
        ]
        assert len(outside_items) == 2
        assert len({item["partId"] for item in outside_items}) == 2
        assert outside_metadata["quality"]["assembly"] == "partial"
        assert any(
            item["code"] == "EXTERNAL_DOCUMENT_IDENTITY_LOCAL"
            for item in outside_metadata["native"]["extraction_diagnostics"]
        )

        independent_source = root / "independent-link-target.FCStd"
        independent_output = root / "independent-link-target.json"
        make_independent_link_target_fixture(independent_source)
        extract_fcstd_part_metadata(str(independent_source), str(independent_output))
        independent = json.loads(independent_output.read_text())
        validate_part_metadata(independent)
        assert independent["geometry"]["shape"]["volume_mm3"] == 12
        assert {item["source"]["objectName"] for item in independent["native"]["occurrences"]} >= {
            "Source",
            "Instance",
        }
        assert independent["quality"]["geometry"] == "partial"

        hidden_source = root / "hidden-link-target.FCStd"
        hidden_output = root / "hidden-link-target.json"
        make_independent_link_target_fixture(hidden_source)
        with zipfile.ZipFile(hidden_source, "a") as archive:
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="Source"><Properties>'
                '<Property name="Visibility" type="App::PropertyBool"><Bool value="false"/>'
                "</Property></Properties></ViewProvider></ViewProviderData></Document>",
            )
        original_hash = hashlib.sha256(hidden_source.read_bytes()).hexdigest()
        extract_fcstd_part_metadata(str(hidden_source), str(hidden_output))
        hidden = json.loads(hidden_output.read_text())
        validate_part_metadata(hidden)
        assert hidden["geometry"]["shape"]["volume_mm3"] == 6
        assert "Source" not in {
            item["source"]["objectName"] for item in hidden["native"]["occurrences"]
        }
        assert not any(
            item["code"] == "LINK_TARGET_VISIBILITY_UNAVAILABLE"
            for item in hidden["native"]["extraction_diagnostics"]
        )
        assert hashlib.sha256(hidden_source.read_bytes()).hexdigest() == original_hash

        hidden_link_source = root / "hidden-link.FCStd"
        hidden_link_package = root / "hidden-link.pfg"
        make_hidden_link_fixture(hidden_link_source)
        convert_fcstd_to_modelryx_package(str(hidden_link_source), str(hidden_link_package))
        hidden_link_metadata = json.loads(
            hidden_link_package.with_suffix(".part-metadata.json").read_text()
        )
        validate_part_metadata(hidden_link_metadata)
        links = {
            item["source"]["objectName"]: item
            for item in hidden_link_metadata["native"]["occurrences"]
            if item["source"]["objectName"] in {"Link0", "Link1"}
        }
        assert links["Link0"]["display"]["visible"] is True
        assert links["Link1"]["display"]["visible"] is False
        rows = hidden_link_metadata["native"]["bom_rows"]
        assert len(rows) == 1 and rows[0]["quantity"] == 2
        assert set(rows[0]["occurrence_ids"]) == {
            links["Link0"]["occurrenceId"],
            links["Link1"]["occurrenceId"],
        }
        assert hidden_link_metadata["geometry"]["shape"]["volume_mm3"] == 6
        assert hidden_link_metadata["quality"]["geometry"] == "partial"
        with zipfile.ZipFile(hidden_link_package) as package:
            diagnostics = json.loads(package.read("diagnostics.json"))
        assert diagnostics["statistics"]["meshes"] == 1

        hidden_group_source = root / "hidden-group.FCStd"
        hidden_group_package = root / "hidden-group.pfg"
        make_hidden_link_fixture(hidden_group_source, hidden_parent=True)
        convert_fcstd_to_modelryx_package(str(hidden_group_source), str(hidden_group_package))
        hidden_group = json.loads(
            hidden_group_package.with_suffix(".part-metadata.json").read_text()
        )
        validate_part_metadata(hidden_group)
        hidden_group_links = [
            item
            for item in hidden_group["native"]["occurrences"]
            if item["source"]["objectName"] in {"Link0", "Link1"}
        ]
        assert len(hidden_group_links) == 2 and all(
            not item["display"]["visible"] for item in hidden_group_links
        )
        assert hidden_group["native"]["bom_rows"][0]["quantity"] == 2
        assert hidden_group["quality"]["geometry"] == "unavailable"
        with zipfile.ZipFile(hidden_group_package) as package:
            hidden_group_diagnostics = json.loads(package.read("diagnostics.json"))
        assert any(
            item["code"] == "NO_VISIBLE_GEOMETRY" for item in hidden_group_diagnostics["warnings"]
        )
        assert not any(
            item["code"] == "CONSTRUCTION_ONLY_DOCUMENT"
            for item in hidden_group_diagnostics["warnings"]
        )

        for case, options in (
            ("suppressed-link", {"suppressed_link": True}),
            ("suppressed-parent", {"suppressed_parent": True}),
            ("unverified-suppression", {"unverified_suppression": True}),
        ):
            case_source = root / f"{case}.FCStd"
            case_output = root / f"{case}.json"
            make_hidden_link_fixture(case_source, **options)
            source_digest = hashlib.sha256(case_source.read_bytes()).hexdigest()
            extract_fcstd_part_metadata(str(case_source), str(case_output))
            assert hashlib.sha256(case_source.read_bytes()).hexdigest() == source_digest
            case_metadata = json.loads(case_output.read_text())
            validate_part_metadata(case_metadata)
            case_links = {
                item["source"]["objectName"]: item
                for item in case_metadata["native"]["occurrences"]
                if item["source"]["objectName"] in {"Link0", "Link1"}
            }
            rows = case_metadata["native"]["bom_rows"]
            if case == "suppressed-link":
                assert case_links["Link1"]["suppressed"] is True
                assert case_links["Link1"]["suppressionSource"] == "native"
                assert rows[0]["quantity"] == 1
                assert rows[0]["occurrence_ids"] == [case_links["Link0"]["occurrenceId"]]
            elif case == "suppressed-parent":
                assert all(
                    item["suppressed"] is True and item["suppressionSource"] == "ancestor"
                    for item in case_links.values()
                )
                assert rows == []
                assert case_metadata["quality"]["geometry"] == "unavailable"
            else:
                assert "suppressed" not in case_links["Link1"]
                assert rows[0]["quantity"] == 2
                assert any(
                    issue["code"] == "ASSEMBLY_SUPPRESSION_UNVERIFIED"
                    for issue in case_metadata["native"]["extraction_diagnostics"]
                )
        assert linked_diagnostics["statistics"]["meshes"] == 1
        assert len(linked_scene.graph.nodes_geometry) == 2
        assert linked_scene.bounds.round(6).tolist() == [
            [-0.005, -0.005, 0.0],
            [0.025, 0.005, 0.012],
        ]

        array_source = root / "link-array.FCStd"
        array_output = root / "link-array.json"
        make_link_array_fixture(array_source)
        from link_array_evidence import link_arrays

        limited_document = FreeCAD.openDocument(str(array_source))
        try:
            limited_array = link_arrays(
                limited_document, placement_value=lambda _: None,
                document_identity=lambda _: "fixture", max_elements=1,
            )[0]
            assert limited_array["truncated"] is True
            assert limited_array["definition_evidence"]["status"] == "uniform"
            assert limited_array["suppression_summary"]["uninspected_count"] == 2
            assert limited_array["elements"][0]["suppression"]["state"] == "unknown"
        finally:
            FreeCAD.closeDocument(limited_document.Name)
        hidden_source = root / "collapsed-all-hidden.FCStd"
        hidden_document = FreeCAD.openDocument(str(array_source))
        try:
            hidden_array = hidden_document.getObject("Fasteners")
            for index in range(hidden_array.ElementCount):
                hidden_array.setElementVisible(str(index), False)
            hidden_document.recompute()
            hidden_document.saveAs(str(hidden_source))
        finally:
            FreeCAD.closeDocument(hidden_document.Name)
        hidden_document = FreeCAD.openDocument(str(hidden_source))
        try:
            hidden_array = hidden_document.getObject("Fasteners")
            assert filtered_expanded_array_shape(hidden_array, Part, 100) == ("empty", None)
            hidden_evidence = link_arrays(
                hidden_document, placement_value=lambda _: None,
                document_identity=lambda _: "fixture", max_elements=100,
            )[0]
            assert hidden_evidence["suppression_summary"]["unknown_inspected_count"] == 3
        finally:
            FreeCAD.closeDocument(hidden_document.Name)
        extract_fcstd_part_metadata(str(array_source), str(array_output))
        array_metadata = json.loads(array_output.read_text())
        validate_part_metadata(array_metadata)
        array = array_metadata["native"]["link_arrays"][0]
        assert array["representation"] == "collapsed"
        assert array["definition_evidence"] == {"copy_on_change": "Disabled", "status": "uniform"}
        assert array["target"] == "ArraySource"
        assert array["element_count"] == 3
        array_occurrence = next(
            item
            for item in array_metadata["native"]["occurrences"]
            if item["source"]["objectName"] == "Fasteners" and "arrayIndex" not in item["source"]
        )
        assert array["container_occurrences"] == [
            {
                "occurrence_id": array_occurrence["occurrenceId"],
                "parent_occurrence_id": array_occurrence["parentOccurrenceId"],
            }
        ]
        assert array["occurrence_resolution"] == "partial_snapshot_index"
        collapsed_occurrences = [
            item
            for item in array_metadata["native"]["occurrences"]
            if item["source"].get("arrayObjectName") == "Fasteners"
        ]
        assert len(collapsed_occurrences) == 3
        assert {item["source"]["arrayIndex"] for item in collapsed_occurrences} == {0, 1, 2}
        assert all(item["source"]["objectName"] == "Fasteners" for item in collapsed_occurrences)
        assert all("suppressed" not in item for item in collapsed_occurrences)
        assert any(
            issue["code"] == "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN"
            and issue.get("object_name") == "Fasteners"
            for issue in array_metadata["native"]["extraction_diagnostics"]
        )
        collapsed_by_index = {item["source"]["arrayIndex"]: item for item in collapsed_occurrences}
        assert collapsed_by_index[1]["display"]["visible"] is False
        assert "visible" not in collapsed_by_index[0]["display"]
        assert all(
            array_occurrence["occurrenceId"] not in row["occurrence_ids"]
            for row in array_metadata["native"]["bom_rows"]
        )
        assert "instance_count" not in array_metadata["assembly"]
        assert [item["placement"]["position_mm"][0] for item in array["elements"]] == [0, 10, 20]
        assert [item["visibility"] for item in array["elements"]] == [True, False, True]
        assert array["elements"][1]["scale"] == [2, 1, 0.5]
        assert array_metadata["quality"]["assembly"] == "partial"

        lost_suppression_source = root / "collapsed-after-suppression.FCStd"
        lost_suppression_output = root / "collapsed-after-suppression.json"
        make_link_array_fixture(
            lost_suppression_source, expanded=True, suppressed_element=True,
            collapse_after_suppression=True,
        )
        extract_fcstd_part_metadata(str(lost_suppression_source), str(lost_suppression_output))
        lost_suppression = json.loads(lost_suppression_output.read_text())
        validate_part_metadata(lost_suppression)
        lost_array = lost_suppression["native"]["link_arrays"][0]
        assert lost_array["representation"] == "collapsed"
        assert all(element["suppression"]["state"] == "unknown" for element in lost_array["elements"])
        assert "instance_count" not in lost_suppression["assembly"]
        assert array_metadata["quality"]["geometry"] == "partial"
        assert array_metadata["extraction"]["capabilities"]["assembly_semantics"] == "partial"
        assert any(
            issue["code"] == "LINK_ARRAY_OCCURRENCES_UNSUPPORTED"
            for issue in array_metadata["native"]["extraction_diagnostics"]
        )

        for expanded in (False, True):
            transformed_source = root / f"transformed-array-{expanded}.FCStd"
            transformed_output = root / f"transformed-array-{expanded}.json"
            make_link_array_fixture(transformed_source, expanded=expanded, transformed=True)
            extract_fcstd_part_metadata(str(transformed_source), str(transformed_output))
            transformed_metadata = json.loads(transformed_output.read_text())
            validate_part_metadata(transformed_metadata)
            element = transformed_metadata["native"]["link_arrays"][0]["elements"][1]
            parent = next(
                item
                for item in transformed_metadata["native"]["occurrences"]
                if item["source"]["objectName"] == "ParentAssembly"
            )
            assert (
                transformed_metadata["native"]["link_arrays"][0]["container_occurrences"][0][
                    "parent_occurrence_id"
                ]
                == parent["occurrenceId"]
            )
            assert element["visibility"] is False
            assert element["transform_to_array_parent"] == {
                "matrix": [[0, -2, 0, 100], [4, 0, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]],
                "translation_unit": "mm",
            }, element["transform_to_array_parent"]
            assert element["world_transforms"][0]["transform"] == {
                "matrix": [[0, 2, 0, 200], [-4, 0, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]],
                "translation_unit": "mm",
            }, element["world_transforms"]
            assert transformed_metadata["quality"]["assembly"] == "partial"

        uncertain_source = root / "uncertain-array-parent.FCStd"
        uncertain_output = root / "uncertain-array-parent.json"
        make_link_array_fixture(uncertain_source, transformed=True, unknown_parent=True)
        extract_fcstd_part_metadata(str(uncertain_source), str(uncertain_output))
        uncertain = json.loads(uncertain_output.read_text())
        validate_part_metadata(uncertain)
        uncertain_element = uncertain["native"]["link_arrays"][0]["elements"][1]
        assert uncertain_element["transform_to_array_parent"] is not None
        assert uncertain_element["world_transforms"][0]["transform"] is None
        assert any(
            issue["code"] == "LINK_ARRAY_WORLD_TRANSFORM_UNAVAILABLE"
            for issue in uncertain["native"]["extraction_diagnostics"]
        )

        suppressed_array_source = root / "suppressed-array-element.FCStd"
        suppressed_array_output = root / "suppressed-array-element.json"
        make_link_array_fixture(
            suppressed_array_source, expanded=True, suppressed_element=True, assigned_material=True
        )
        extract_fcstd_part_metadata(str(suppressed_array_source), str(suppressed_array_output))
        suppressed_array = json.loads(suppressed_array_output.read_text())
        validate_part_metadata(suppressed_array)
        elements = suppressed_array["native"]["link_arrays"][0]["elements"]
        assert elements[1]["visibility"] is False
        assert elements[1]["suppression"]["state"] in {"unknown", "active"}, elements[1]
        if elements[1]["suppression"]["state"] == "active":
            assert elements[1]["suppression"]["source"] == "native"
        assert elements[2]["visibility"] is True
        assert elements[2]["suppression"] == {"state": "suppressed", "source": "native"}, (
            "FreeCAD did not preserve explicit array-element suppression after save and reopen: "
            f"{elements[2]['suppression']}"
        )
        assert suppressed_array["quality"]["geometry"] == "partial"
        assert suppressed_array["physical"]["density_kg_m3"] == 7850
        assert "mass_kg" not in suppressed_array["physical"]
        assert "center_of_mass" not in suppressed_array["physical"]
        assert suppressed_array["quality"]["physical"] == "partial"
        assert any(
            issue["code"] == "LINK_ARRAY_SUPPRESSED_GEOMETRY_UNVERIFIED"
            for issue in suppressed_array["native"]["extraction_diagnostics"]
        )

        verified_source = root / "verified-array.FCStd"
        verified_output = root / "verified-array.json"
        make_link_array_fixture(
            verified_source, expanded=True, explicit_element_states=True, extra_link=True
        )
        with zipfile.ZipFile(verified_source, "a") as archive:
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="ArraySource"><Properties>'
                '<Property name="Visibility"><Bool value="false"/></Property>'
                "</Properties></ViewProvider></ViewProviderData></Document>",
            )
        original_digest = hashlib.sha256(verified_source.read_bytes()).hexdigest()
        verified_document = FreeCAD.openDocument(str(verified_source))
        try:
            verified_link = verified_document.getObject("Fasteners")
            status, shape = filtered_expanded_array_shape(verified_link, Part, 100)
            assert status == "filtered"
            assert abs(shape.Volume - 24.0) < 1e-7
            assert len(shape.Solids) == 1
            assert shape.BoundBox.XMin == 0 and shape.BoundBox.XMax == 2
        finally:
            FreeCAD.closeDocument(verified_document.Name)
        transformed_filtered_source = root / "filtered-array-transform.FCStd"
        make_link_array_fixture(
            transformed_filtered_source, expanded=True, transformed=True, explicit_element_states=True
        )
        transformed_filtered_document = FreeCAD.openDocument(str(transformed_filtered_source))
        try:
            transformed_link = transformed_filtered_document.getObject("Fasteners")
            status, shape = filtered_expanded_array_shape(transformed_link, Part, 100)
            assert status == "filtered"
            assert abs(shape.Volume - 192.0) < 1e-7
            assert abs(shape.BoundBox.XMin - 94.0) < 1e-7
            assert abs(shape.BoundBox.XMax - 100.0) < 1e-7
            assert abs(shape.BoundBox.YMax - 4.0) < 1e-7
        finally:
            FreeCAD.closeDocument(transformed_filtered_document.Name)
        with zipfile.ZipFile(transformed_filtered_source, "a") as archive:
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="ArraySource"><Properties>'
                '<Property name="Visibility"><Bool value="false"/></Property>'
                "</Properties></ViewProvider></ViewProviderData></Document>",
            )
        transformed_package = root / "filtered-array-transform.pfg"
        convert_fcstd_to_modelryx_package(
            str(transformed_filtered_source), str(transformed_package), keep_exploded=True
        )
        transformed_metadata = json.loads(transformed_package.with_suffix(".part-metadata.json").read_text())
        validate_part_metadata(transformed_metadata)
        assert abs(transformed_metadata["geometry"]["shape"]["volume_mm3"] - 192.0) < 1e-7
        transformed_scene = trimesh.load_scene(transformed_package.with_suffix(".glb"))
        assert transformed_scene.bounds.round(6).tolist() == [[0.2, 0.036, 0.0], [0.206, 0.04, 0.008]]
        extract_fcstd_part_metadata(str(verified_source), str(verified_output))
        assert hashlib.sha256(verified_source.read_bytes()).hexdigest() == original_digest
        verified = json.loads(verified_output.read_text())
        validate_part_metadata(verified)
        verified_array = verified["native"]["link_arrays"][0]
        assert not any(
            issue["code"] == "LINK_ARRAY_ELEMENT_ACTIVITY_UNKNOWN"
            and issue.get("object_name") == "Fasteners"
            for issue in verified["native"]["extraction_diagnostics"]
        )
        assert verified_array["occurrence_resolution"] == "verified_snapshot_index"
        elements = [
            item
            for item in verified["native"]["occurrences"]
            if item["source"].get("typeId") == "App::LinkElement"
        ]
        assert len(elements) == 3
        assert sum(item["suppressed"] for item in elements) == 1
        assert sum(item["display"]["visible"] for item in elements) == 1
        assert all(item["source"]["identityBasis"] == "array_index" for item in elements)
        regular_link = next(
            item
            for item in verified["native"]["occurrences"]
            if item["source"]["objectName"] == "RegularLink"
        )
        assert verified["native"]["bom_rows"][0]["quantity"] == 3
        assert set(verified["native"]["bom_rows"][0]["occurrence_ids"]) == {
            item["occurrenceId"] for item in elements if not item["suppressed"]
        } | {regular_link["occurrenceId"]}
        assert verified["assembly"]["instance_count"] == 3
        assert abs(verified["geometry"]["shape"]["volume_mm3"] - 48.0) < 1e-7
        verified_package = root / "verified-array.pfg"
        convert_fcstd_to_modelryx_package(str(verified_source), str(verified_package), keep_exploded=True)
        verified_scene = trimesh.load_scene(verified_package.with_suffix(".glb"))
        assert len(verified_scene.graph.nodes_geometry) == 2
        assert verified_scene.bounds.round(6).tolist() == [[0.0, 0.0, 0.0], [0.042, 0.003, 0.004]]

        colored_array_source = root / "filtered-array-colors.FCStd"
        make_link_array_fixture(colored_array_source, expanded=True, explicit_element_states=True)
        retained_colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (255, 0, 255), (0, 255, 255),
        ]
        excluded_color = (30, 40, 50)
        colors = retained_colors + [excluded_color] * 6
        material_data = len(colors).to_bytes(4, "little") + b"".join(
            bytes((0, 0, 0, 0)) + bytes((255, blue, green, red)) for red, green, blue in colors
        )
        with zipfile.ZipFile(colored_array_source, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("ArrayAppearance.bin", material_data)
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="ArraySource"><Properties>'
                '<Property name="Visibility"><Bool value="false"/></Property>'
                '</Properties></ViewProvider><ViewProvider name="Fasteners"><Properties>'
                '<Property name="ShapeAppearance" type="App::PropertyMaterialList">'
                '<MaterialList file="ArrayAppearance.bin"/></Property></Properties></ViewProvider>'
                '</ViewProviderData></Document>',
            )
        colored_array_package = root / "filtered-array-colors.pfg"
        convert_fcstd_to_modelryx_package(str(colored_array_source), str(colored_array_package), keep_exploded=True)
        colored_scene = trimesh.load_scene(colored_array_package.with_suffix(".glb"))
        actual_colors = {
            tuple(int(channel) for channel in color[:3])
            for geometry in colored_scene.geometry.values()
            for color in geometry.visual.face_colors
        }
        assert set(retained_colors) <= actual_colors, actual_colors
        assert excluded_color not in actual_colors

        ambiguous_color_source = root / "overlapping-array-colors.FCStd"
        make_link_array_fixture(
            ambiguous_color_source, expanded=True, explicit_element_states=True, overlap_active=True,
        )
        overlap_colors = [(255, 0, 0)] * 6 + [(0, 0, 255)] * 6 + [(0, 255, 0)] * 6
        overlap_material = len(overlap_colors).to_bytes(4, "little") + b"".join(
            bytes((0, 0, 0, 0)) + bytes((255, blue, green, red))
            for red, green, blue in overlap_colors
        )
        with zipfile.ZipFile(ambiguous_color_source, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("OverlappingAppearance.bin", overlap_material)
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="ArraySource"><Properties>'
                '<Property name="Visibility"><Bool value="false"/></Property>'
                '</Properties></ViewProvider><ViewProvider name="Fasteners"><Properties>'
                '<Property name="ShapeAppearance" type="App::PropertyMaterialList">'
                '<MaterialList file="OverlappingAppearance.bin"/></Property></Properties></ViewProvider>'
                '</ViewProviderData></Document>',
            )
        ambiguous_color_package = root / "overlapping-array-colors.pfg"
        convert_fcstd_to_modelryx_package(str(ambiguous_color_source), str(ambiguous_color_package))
        ambiguous_color_metadata = json.loads(
            ambiguous_color_package.with_suffix(".part-metadata.json").read_text()
        )
        validate_part_metadata(ambiguous_color_metadata)
        assert any(
            issue["code"] == "LINK_ARRAY_FILTERED_FACE_APPEARANCE_UNAVAILABLE"
            for issue in ambiguous_color_metadata["native"]["extraction_diagnostics"]
        )

        all_suppressed_source = root / "all-elements-suppressed.FCStd"
        make_link_array_fixture(
            all_suppressed_source, expanded=True, explicit_element_states=True,
            all_elements_suppressed=True, extra_link=True,
        )
        with zipfile.ZipFile(all_suppressed_source, "a") as archive:
            archive.writestr(
                "GuiDocument.xml",
                '<Document><ViewProviderData><ViewProvider name="ArraySource"><Properties>'
                '<Property name="Visibility"><Bool value="false"/></Property>'
                "</Properties></ViewProvider></ViewProviderData></Document>",
            )
        all_suppressed_package = root / "all-elements-suppressed.pfg"
        convert_fcstd_to_modelryx_package(
            str(all_suppressed_source), str(all_suppressed_package), keep_exploded=True
        )
        all_suppressed_metadata = json.loads(
            all_suppressed_package.with_suffix(".part-metadata.json").read_text()
        )
        validate_part_metadata(all_suppressed_metadata)
        assert all_suppressed_metadata["assembly"]["instance_count"] == 1
        assert abs(all_suppressed_metadata["geometry"]["shape"]["volume_mm3"] - 24.0) < 1e-7
        all_suppressed_scene = trimesh.load_scene(all_suppressed_package.with_suffix(".glb"))
        assert len(all_suppressed_scene.graph.nodes_geometry) == 1
        assert all_suppressed_scene.bounds.round(6).tolist() == [[0.04, 0.0, 0.0], [0.042, 0.003, 0.004]]
        assert verified["quality"]["assembly"] == "partial"
        assert verified["quality"]["geometry"] == "partial"
        verified_material_source = root / "verified-material-array.FCStd"
        verified_material_output = root / "verified-material-array.json"
        make_link_array_fixture(
            verified_material_source, expanded=True, explicit_element_states=True, assigned_material=True
        )
        extract_fcstd_part_metadata(str(verified_material_source), str(verified_material_output))
        verified_material = json.loads(verified_material_output.read_text())
        validate_part_metadata(verified_material)
        assert abs(verified_material["physical"]["mass_kg"] - 0.0003768) < 1e-9
        repeated_output = root / "verified-array-repeat.json"
        extract_fcstd_part_metadata(str(verified_source), str(repeated_output))
        repeated = json.loads(repeated_output.read_text())
        assert {item["occurrenceId"] for item in elements} == {
            item["occurrenceId"]
            for item in repeated["native"]["occurrences"]
            if item["source"].get("typeId") == "App::LinkElement"
        }

        variant_source = root / "configured-array.FCStd"
        variant_output = root / "configured-array.json"
        make_link_array_fixture(
            variant_source, expanded=True, explicit_element_states=True, variant=True
        )
        extract_fcstd_part_metadata(str(variant_source), str(variant_output))
        variant_metadata = json.loads(variant_output.read_text())
        validate_part_metadata(variant_metadata)
        assert "occurrence_resolution" not in variant_metadata["native"]["link_arrays"][0]
        assert "instance_count" not in variant_metadata["assembly"]
        assert any(
            issue["code"] == "LINK_ARRAY_BOM_QUANTITY_UNAVAILABLE"
            for issue in variant_metadata["native"]["extraction_diagnostics"]
        )

        copy_source = root / "copy-on-change-array.FCStd"
        copy_output = root / "copy-on-change-array.json"
        make_link_array_fixture(copy_source, expanded=True, explicit_element_states=True, copy_on_change=True)
        extract_fcstd_part_metadata(str(copy_source), str(copy_output))
        copy_metadata = json.loads(copy_output.read_text())
        validate_part_metadata(copy_metadata)
        copy_array = copy_metadata["native"]["link_arrays"][0]
        assert copy_array["definition_evidence"]["status"] == "owned_uniform", copy_array
        assert copy_array["definition_evidence"]["copy_on_change"] == "Owned"
        assert copy_array["target"] != "ArraySource"
        assert copy_array["copy_on_change_source"]["object"] == "ArraySource"
        assert copy_array["copy_on_change_group"] == "CopyOnChangeGroup"
        assert all(
            element["definition"]["target"] and element["definition"]["document"]
            and element["definition"]["copy_on_change"] in {"Disabled", "Enabled", "Owned", "Tracking"}
            for element in copy_array["elements"]
        ), copy_array
        assert copy_array["occurrence_resolution"] == "verified_snapshot_index"
        assert copy_metadata["assembly"]["instance_count"] == 3
        configured_rows = [
            row for row in copy_metadata["native"]["bom_rows"]
            if row["source"].get("linkedObjectName") == copy_array["target"]
        ]
        assert len(configured_rows) == 1 and configured_rows[0]["quantity"] == 2
        assert configured_rows[0]["properties"]["Variant"] == "Modified"
        assert configured_rows[0]["part_id"] != next(
            row["part_id"] for row in copy_metadata["native"]["bom_rows"]
            if row["source"]["objectName"] == "ArraySource"
        )
        assert not any(
            row["source"]["objectName"] == "CopyOnChangeGroup"
            for row in copy_metadata["native"]["bom_rows"]
        )
        assert not any(
            item["source"]["objectName"] == "CopyOnChangeGroup"
            for item in copy_metadata["native"]["occurrences"]
        )
        assert abs(copy_metadata["geometry"]["shape"]["volume_mm3"] - 48.0) < 1e-7

        unrelated_copy_source = root / "copy-with-unrelated-selector.FCStd"
        unrelated_copy_output = root / "copy-with-unrelated-selector.json"
        make_link_array_fixture(
            unrelated_copy_source, expanded=True, explicit_element_states=True,
            copy_on_change=True, unrelated_selector=True,
        )
        extract_fcstd_part_metadata(str(unrelated_copy_source), str(unrelated_copy_output))
        unrelated_copy = json.loads(unrelated_copy_output.read_text())
        validate_part_metadata(unrelated_copy)
        assert unrelated_copy["native"]["link_arrays"][0]["definition_evidence"]["status"] == "owned_uniform"
        assert "occurrence_resolution" not in unrelated_copy["native"]["link_arrays"][0]
        assert "instance_count" not in unrelated_copy["assembly"]

        regular_group_source = root / "regular-link-group.FCStd"
        regular_group_output = root / "regular-link-group.json"
        make_regular_link_group_fixture(regular_group_source)
        extract_fcstd_part_metadata(str(regular_group_source), str(regular_group_output))
        regular_group_metadata = json.loads(regular_group_output.read_text())
        validate_part_metadata(regular_group_metadata)
        assert any(
            item["source"]["objectName"] == "UserGroup"
            for item in regular_group_metadata["native"]["occurrences"]
        )
        assert any(
            item["source"]["objectName"] == "UserGroup"
            for item in regular_group_metadata["native"]["bom_rows"]
        )

        inactive_source = root / "suppressed-array.FCStd"
        inactive_output = root / "suppressed-array.json"
        make_link_array_fixture(inactive_source, suppressed_array=True)
        extract_fcstd_part_metadata(str(inactive_source), str(inactive_output))
        inactive = json.loads(inactive_output.read_text())
        validate_part_metadata(inactive)
        inactive_array = next(
            item
            for item in inactive["native"]["occurrences"]
            if item["source"]["objectName"] == "Fasteners" and "arrayIndex" not in item["source"]
        )
        assert inactive_array["suppressed"] is True
        assert inactive["assembly"]["instance_count"] == 1
        assert not any(
            issue["code"] == "LINK_ARRAY_BOM_QUANTITY_UNAVAILABLE"
            for issue in inactive["native"]["extraction_diagnostics"]
        )

        expanded_source = root / "expanded-link-array.FCStd"
        expanded_package = root / "expanded-link-array.pfg"
        make_link_array_fixture(expanded_source, expanded=True)
        convert_fcstd_to_modelryx_package(str(expanded_source), str(expanded_package))
        expanded_metadata = json.loads(
            expanded_package.with_suffix(".part-metadata.json").read_text()
        )
        validate_part_metadata(expanded_metadata)
        assert expanded_metadata["native"]["link_arrays"][0]["representation"] == "expanded"
        assert expanded_metadata["native"]["link_arrays"][0]["elements"][1]["scale"] == [2, 1, 0.5]
        assert expanded_metadata["geometry"]["shape"]["volume_mm3"] == 72, expanded_metadata[
            "geometry"
        ]["shape"]["volume_mm3"]
        expanded_occurrences = [
            item
            for item in expanded_metadata["native"]["occurrences"]
            if item["source"].get("typeId") == "App::LinkElement"
        ]
        assert len(expanded_occurrences) == 3
        with zipfile.ZipFile(expanded_package) as package:
            expanded_diagnostics = json.loads(package.read("diagnostics.json"))
            expanded_preview = json.loads(package.read("model-preview.json"))
        assert expanded_diagnostics["statistics"]["meshes"] == 2
        assert not any(item["cadType"] == "LinkElement" for item in expanded_preview["items"])

        claimed_source = root / "claimed-children.FCStd"
        claimed_package = root / "claimed-children.pfg"
        make_claimed_children_fixture(claimed_source)
        convert_fcstd_to_modelryx_package(str(claimed_source), str(claimed_package))
        with zipfile.ZipFile(claimed_package) as package:
            claimed_preview = json.loads(package.read("model-preview.json"))
            claimed_diagnostics = json.loads(package.read("diagnostics.json"))
        claimed_metadata = json.loads(
            claimed_package.with_suffix(".part-metadata.json").read_text()
        )
        assert claimed_metadata["quality"]["features"] == "partial"
        assert {item["type"] for item in claimed_metadata["features"]["items"]} == {
            "UNKNOWN_FEATURE"
        }
        claimed_items = claimed_preview["items"]
        assert (
            next(item for item in claimed_items if item["name"] == "Exploded result")["cadType"]
            == "Group"
        )
        results = [item for item in claimed_items if item["name"].startswith("Result ")]
        assert len(results) == 2
        for result in results:
            operations = [
                item
                for item in claimed_items
                if item["parentOccurrenceId"] == result["occurrenceId"]
                and item["name"] == "Shared operation"
            ]
            assert len(operations) == 1
            assert any(
                item["parentOccurrenceId"] == operations[0]["occurrenceId"]
                and item["name"] == "Source solid"
                for item in claimed_items
            )
        # Both result occurrences instance one cached mesh; linked history is navigation-only.
        assert claimed_diagnostics["statistics"]["meshes"] == 1

        construction_source = root / "construction-only.FCStd"
        construction_package = root / "construction-only.pfg"
        make_construction_only_fixture(construction_source)
        convert_fcstd_to_modelryx_package(
            str(construction_source),
            str(construction_package),
            tessellation_profile="draft",
        )
        with zipfile.ZipFile(construction_package) as package:
            construction_manifest = json.loads(package.read("manifest.json"))
            construction_diagnostics = json.loads(package.read("diagnostics.json"))
        construction_metadata = json.loads(
            construction_package.with_suffix(".part-metadata.json").read_text()
        )
        assert construction_manifest["converter"]["tessellationProfile"] == "draft"
        assert construction_diagnostics["statistics"]["meshes"] == 0
        assert construction_diagnostics["warnings"][0]["code"] == "CONSTRUCTION_ONLY_DOCUMENT"
        assert construction_metadata["geometry"]["shape"]["volume_mm3"] == 0.0
        assert construction_metadata["quality"]["geometry"] == "unavailable"
        assert any(
            "construction" in warning.lower()
            for warning in construction_metadata["quality"]["warnings"]
        )

        broken_source = root / "partially-broken.FCStd"
        broken_metadata_path = root / "partially-broken.json"
        make_partially_broken_fixture(broken_source)
        extract_fcstd_part_metadata(str(broken_source), str(broken_metadata_path))
        broken_metadata = json.loads(broken_metadata_path.read_text())
        validate_part_metadata(broken_metadata)
        assert broken_metadata["quality"]["geometry"] == "partial"
        assert broken_metadata["geometry"]["shape"]["volume_mm3"] == 192.0
        health = broken_metadata["native"]["document_health"]
        assert health["recompute_succeeded"] is True
        assert health["objects_checked"] == 2
        assert any(issue["code"] == "NULL_FEATURE_SHAPE" for issue in health["issues"])
        assert broken_metadata["extraction"]["sections"]["geometry"]["records_skipped"] == 1

        expression_source = root / "broken-expression.FCStd"
        expression_metadata_path = root / "broken-expression.json"
        make_broken_expression_fixture(expression_source)
        extract_fcstd_part_metadata(str(expression_source), str(expression_metadata_path))
        expression_metadata = json.loads(expression_metadata_path.read_text())
        validate_part_metadata(expression_metadata)
        assert expression_metadata["quality"]["geometry"] == "partial"
        assert (
            expression_metadata["extraction"]["capabilities"]["parameter_dependencies"] == "partial"
        )
        assert any(
            issue["code"] == "BROKEN_EXPRESSION" and issue["object_name"] == "Box"
            for issue in expression_metadata["native"]["document_health"]["issues"]
        )

        face_color_source = root / "face-colors.FCStd"
        face_color_package = root / "face-colors.pfg"
        make_face_color_fixture(face_color_source)
        convert_fcstd_to_modelryx_package(str(face_color_source), str(face_color_package))
        with zipfile.ZipFile(face_color_package) as package:
            face_color_glb = root / "face-colors.glb"
            face_color_glb.write_bytes(package.read("model.glb"))
        face_color_scene = trimesh.load_scene(face_color_glb)
        exported_colors = {
            tuple(color[:3])
            for geometry in face_color_scene.geometry.values()
            for color in geometry.visual.face_colors
        }
        assert exported_colors == {
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
        }
        leaf = FreeCAD.newDocument("ExternalBase")
        base_sheet = leaf.addObject("Spreadsheet::Sheet", "Sheet")
        base_sheet.set("A1", "6 mm")
        base_sheet.setAlias("A1", "Base")
        leaf.recompute()
        leaf.saveAs(str(root / "external-base.FCStd"))
        remote = FreeCAD.newDocument("ExternalParameterSource")
        sheet = remote.addObject("Spreadsheet::Sheet", "Sheet")
        remote.saveAs(str(root / "external-parameters.FCStd"))
        sheet.set("A2", "=ExternalBase#Sheet.Base")
        sheet.setAlias("A2", "Base")
        sheet.set("A1", "=Base * 2")
        sheet.setAlias("A1", "Thickness")
        remote.recompute()
        remote.save()
        owner = FreeCAD.newDocument("ExternalParameterOwner")
        box = owner.addObject("Part::Box", "Box")
        owner.saveAs(str(root / "parameter-owner.FCStd"))
        box.setExpression("Length", "ExternalParameterSource#Sheet.Thickness")
        owner.recompute()
        owner.save()
        FreeCAD.closeDocument(owner.Name)
        FreeCAD.closeDocument(remote.Name)
        FreeCAD.closeDocument(leaf.Name)
        external_metadata_path = root / "parameter-owner.json"
        extract_fcstd_part_metadata(str(root / "parameter-owner.FCStd"), str(external_metadata_path))
        external_metadata = json.loads(external_metadata_path.read_text())
        validate_part_metadata(external_metadata)
        evidence = external_metadata["native"]["parameter_graph"]["external_references"]
        assert len(evidence) == 1 and evidence[0]["status"] == "resolved", evidence
        assert evidence[0]["source_document"] == "external-parameters.FCStd"
        assert evidence[0]["source_parameter"] == "Sheet.A1"
        assert evidence[0]["value"] == 12.0 and evidence[0]["unit"] == "mm"
        graph = external_metadata["native"]["parameter_graph"]
        assert graph["external_edges"] == [{
            "source": {"document": "external-parameters.FCStd", "object": "Sheet", "parameter": "Sheet.A1"},
            "target": {"document": "parameter-owner.FCStd", "node_id": "Box.Length"},
            "reference": evidence[0]["reference"],
        }]
        assert graph["cross_document_cycles_status"] == "partial"
        assert graph["external_documents_inspected"] == 2
        assert graph["external_document_limit_reached"] is False
        assert graph["external_upstream"]["edges"] == [{
            "document": "external-parameters.FCStd", "source": "Sheet.A2",
            "target": "Sheet.A1", "reference": "Base",
        }]
        assert graph["external_upstream"]["cross_document_edges"] == [{
            "source": {"document": "external-base.FCStd", "node_id": "Sheet.A1"},
            "target": {"document": "external-parameters.FCStd", "node_id": "Sheet.A2"},
            "reference": "external_base#Sheet.Base",
        }]
    print("FreeCAD golden integration test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
