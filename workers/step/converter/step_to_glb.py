"""
STEP to GLB Converter using OpenCASCADE

This module provides functionality to convert STEP CAD files to GLB format
using pythonocc-core (OpenCASCADE Python bindings).
"""

import json
import logging
import sys
from pathlib import Path

import numpy as np
from glb_utils import export_double_sided_glb
from step_cli import build_parser, run_validation
from step_metadata import extract_part_metadata

try:
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.gp import gp_Pnt
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import (
        GCPnts_AbscissaPoint,
        GCPnts_QuasiUniformDeflection,
        GCPnts_UniformAbscissa,
    )
    import trimesh
except ImportError as e:
    logging.getLogger(__name__).error(
        "Error importing required libraries: %s. Install pythonocc-core and trimesh.",
        e,
    )
    sys.exit(1)

logger = logging.getLogger(__name__)
_warned_conditions: set[str] = set()


def warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned_conditions:
        return
    _warned_conditions.add(key)
    logger.warning(message, *args)


class StepToGlbConverter:
    """Convert STEP files to GLB format using OpenCASCADE."""
    
    def __init__(self, tessellation_quality=0.5, use_angular_deflection=True, export_edges=True):
        """
        Initialize the converter.
        
        Args:
            tessellation_quality (float): Quality of mesh tessellation (0.1 to 1.0).
                                         Lower values = finer mesh, larger file.
            use_angular_deflection (bool): Use angular deflection for better curved surface representation.
            export_edges (bool): Export edges as line geometry for "Shaded with Edges" display mode.
        """
        self.tessellation_quality = max(0.1, min(1.0, tessellation_quality))
        self.use_angular_deflection = use_angular_deflection
        self.export_edges = export_edges
        # Angular deflection in radians - controls tessellation of curved surfaces
        # Smaller angle = more triangles on curves for smoother appearance
        self.angular_deflection = 0.5 if tessellation_quality > 0.7 else 0.3 if tessellation_quality > 0.4 else 0.1
        # Edge discretization: number of points per unit length
        self.edge_discretization = 1.0 / self.tessellation_quality

    def discretize_edge(self, edge):
        """Sample one B-Rep edge by geometric deflection, not parameter span."""
        if BRep_Tool.Degenerated(edge):
            return []
        curve_adaptor = BRepAdaptor_Curve(edge)
        first_param = curve_adaptor.FirstParameter()
        last_param = curve_adaptor.LastParameter()
        deflection_sampler = GCPnts_QuasiUniformDeflection(
            curve_adaptor,
            self.tessellation_quality,
            first_param,
            last_param,
        )
        parameters = {first_param, last_param}
        if deflection_sampler.IsDone():
            parameters.update(
                deflection_sampler.Parameter(index)
                for index in range(1, deflection_sampler.NbPoints() + 1)
            )
        curve_length = GCPnts_AbscissaPoint.Length(
            curve_adaptor, first_param, last_param
        )
        maximum_segment_length = max(self.tessellation_quality * 4.0, 0.1)
        point_count = min(
            20000,
            max(2, int(np.ceil(curve_length / maximum_segment_length)) + 1),
        )
        length_sampler = GCPnts_UniformAbscissa(
            curve_adaptor, point_count, first_param, last_param
        )
        if length_sampler.IsDone():
            parameters.update(
                length_sampler.Parameter(index)
                for index in range(1, length_sampler.NbPoints() + 1)
            )
        parameters = sorted(parameters, reverse=first_param > last_param)
        result = []
        for parameter in parameters:
            point = curve_adaptor.Value(parameter)
            candidate = [point.X(), point.Y(), point.Z()]
            if not np.isfinite(candidate).all():
                continue
            if result and np.linalg.norm(np.asarray(candidate) - np.asarray(result[-1])) <= 1e-9:
                continue
            result.append(candidate)
        return result if len(result) >= 2 else []
    
    def read_step_file(self, step_file_path):
        """
        Read a STEP file and return the shape.
        
        Args:
            step_file_path (str): Path to the STEP file.
            
        Returns:
            TopoDS_Shape: The shape read from the STEP file.
            
        Raises:
            FileNotFoundError: If the STEP file doesn't exist.
            ValueError: If the STEP file cannot be read.
        """
        step_path = Path(step_file_path)
        if not step_path.exists():
            raise FileNotFoundError(f"STEP file not found: {step_file_path}")
        
        logger.info("Reading STEP file: %s", step_file_path)
        
        # Create STEP reader
        step_reader = STEPControl_Reader()
        status = step_reader.ReadFile(str(step_path))
        
        if status != IFSelect_RetDone:
            raise ValueError(f"Error reading STEP file: {step_file_path}")
        
        # Transfer roots
        step_reader.TransferRoots()
        shape = step_reader.OneShape()
        
        logger.info("Successfully read STEP file")
        return shape

    def extract_metadata(self, shape, source_name):
        """Build CAD-neutral engineering properties from exact STEP geometry."""
        return extract_part_metadata(shape, source_name)
    
    def tessellate_shape(self, shape):
        """
        Tessellate the shape to create a mesh.
        
        Args:
            shape: The TopoDS_Shape to tessellate.
            
        Returns:
            tuple: (vertices, faces) arrays for the mesh.
        """
        logger.info(
            "Tessellating shape quality=%s angular=%s",
            self.tessellation_quality,
            self.angular_deflection if self.use_angular_deflection else "off",
        )
        
        # Perform incremental mesh tessellation with both linear and angular deflection
        if self.use_angular_deflection:
            mesh = BRepMesh_IncrementalMesh(shape, self.tessellation_quality, False, self.angular_deflection, True)
        else:
            mesh = BRepMesh_IncrementalMesh(shape, self.tessellation_quality)
        mesh.Perform()
        
        if not mesh.IsDone():
            raise RuntimeError("Tessellation failed")
        
        # Extract vertices and faces from all faces in the shape
        vertices = []
        faces = []
        vertex_offset = 0
        
        # Explore all faces in the shape
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        face_count = 0
        
        while explorer.More():
            face = explorer.Current()
            location = TopLoc_Location()
            facing = BRep_Tool.Triangulation(face, location)
            
            if facing is not None:
                face_count += 1
                
                # Get transformation
                transform = location.Transformation()
                
                # Extract vertices
                face_vertices = []
                for i in range(1, facing.NbNodes() + 1):
                    pnt = facing.Node(i)
                    pnt.Transform(transform)
                    face_vertices.append([pnt.X(), pnt.Y(), pnt.Z()])
                
                # Extract triangles
                for i in range(1, facing.NbTriangles() + 1):
                    triangle = facing.Triangle(i)
                    n1, n2, n3 = triangle.Get()
                    
                    # Check face orientation
                    if face.Orientation() == 1:  # TopAbs_REVERSED
                        faces.append([
                            vertex_offset + n1 - 1,
                            vertex_offset + n3 - 1,
                            vertex_offset + n2 - 1
                        ])
                    else:
                        faces.append([
                            vertex_offset + n1 - 1,
                            vertex_offset + n2 - 1,
                            vertex_offset + n3 - 1
                        ])
                
                vertices.extend(face_vertices)
                vertex_offset = len(vertices)
            
            explorer.Next()
        
        logger.info(
            "Tessellation complete faces=%s vertices=%s triangles=%s",
            face_count,
            len(vertices),
            len(faces),
        )
        
        if len(vertices) == 0 or len(faces) == 0:
            raise ValueError("No mesh data extracted from shape")
        
        return np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int32)

    def tessellate_solids(self, shape):
        """
        Tessellate each solid in the shape separately and return a list of
        (vertices, faces) tuples — one per solid.
        """
        from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE

        solids_meshes = []
        solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        solid_count = 0
        while solid_explorer.More():
            solid = solid_explorer.Current()
            solid_count += 1
            
            # Tessellate this solid with quality parameters
            if self.use_angular_deflection:
                mesh = BRepMesh_IncrementalMesh(solid, self.tessellation_quality, False, self.angular_deflection, True)
            else:
                mesh = BRepMesh_IncrementalMesh(solid, self.tessellation_quality)
            mesh.Perform()
            
            # Collect faces that belong to this solid
            face_exp = TopExp_Explorer(solid, TopAbs_FACE)
            vertices = []
            faces = []
            vertex_offset = 0
            while face_exp.More():
                face = face_exp.Current()
                location = TopLoc_Location()
                triang = BRep_Tool.Triangulation(face, location)
                if triang is not None:
                    transform = location.Transformation()
                    face_vertices = []
                    for i in range(1, triang.NbNodes() + 1):
                        pnt = triang.Node(i)
                        pnt.Transform(transform)
                        face_vertices.append([pnt.X(), pnt.Y(), pnt.Z()])

                    for i in range(1, triang.NbTriangles() + 1):
                        triangle = triang.Triangle(i)
                        n1, n2, n3 = triangle.Get()
                        if face.Orientation() == 1:  # TopAbs_REVERSED
                            faces.append([
                                vertex_offset + n1 - 1,
                                vertex_offset + n3 - 1,
                                vertex_offset + n2 - 1
                            ])
                        else:
                            faces.append([
                                vertex_offset + n1 - 1,
                                vertex_offset + n2 - 1,
                                vertex_offset + n3 - 1
                            ])

                    vertices.extend(face_vertices)
                    vertex_offset = len(vertices)

                face_exp.Next()

            if len(vertices) > 0 and len(faces) > 0:
                solids_meshes.append((np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int32)))

            solid_explorer.Next()
        
        logger.info("Tessellated solids=%s meshes=%s", solid_count, len(solids_meshes))
        return solids_meshes

    def extract_edges(self, shape):
        """
        Extract edges from a shape as line segments.
        
        Args:
            shape: The TopoDS_Shape to extract edges from.
            
        Returns:
            tuple: (edge_vertices, edge_indices) arrays for line geometry.
                  edge_indices contains pairs of vertex indices forming line segments.
        """
        if not self.export_edges:
            return np.array([], dtype=np.float64).reshape(0, 3), np.array([], dtype=np.int32).reshape(0, 2)
        
        logger.info("Extracting edges")
        
        edge_vertices = []
        edge_indices = []
        vertex_offset = 0
        edge_count = 0
        
        # Explore all edges in the shape
        edge_explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        
        while edge_explorer.More():
            edge = edge_explorer.Current()
            edge_count += 1
            
            try:
                segment_vertices = self.discretize_edge(edge)
                if len(segment_vertices) < 2:
                    edge_explorer.Next()
                    continue
                
                # Add vertices and create line segments
                start_idx = vertex_offset
                edge_vertices.extend(segment_vertices)
                
                # Create line segments connecting consecutive points
                for i in range(len(segment_vertices) - 1):
                    edge_indices.append([start_idx + i, start_idx + i + 1])
                
                vertex_offset = len(edge_vertices)
                
            except Exception as e:
                warn_once(
                    "edge-extraction",
                    "Could not process at least one edge during extraction: %s",
                    e,
                )
                continue
            
            edge_explorer.Next()
        
        logger.info(
            "Edge extraction complete edges=%s vertices=%s segments=%s",
            edge_count,
            len(edge_vertices),
            len(edge_indices),
        )
        
        if len(edge_vertices) == 0:
            return np.array([], dtype=np.float64).reshape(0, 3), np.array([], dtype=np.int32).reshape(0, 2)
        
        return np.array(edge_vertices, dtype=np.float64), np.array(edge_indices, dtype=np.int32)

    def extract_edges_per_solid(self, shape):
        """
        Extract edges from each solid separately.
        
        Returns:
            list: List of (edge_vertices, edge_indices) tuples, one per solid.
        """
        from OCC.Core.TopAbs import TopAbs_SOLID
        
        if not self.export_edges:
            return []
        
        solids_edges = []
        solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        solid_count = 0
        
        while solid_explorer.More():
            solid = solid_explorer.Current()
            solid_count += 1
            
            edge_vertices = []
            edge_indices = []
            vertex_offset = 0
            
            # Explore edges in this solid
            edge_exp = TopExp_Explorer(solid, TopAbs_EDGE)
            
            while edge_exp.More():
                edge = edge_exp.Current()
                
                try:
                    segment_vertices = self.discretize_edge(edge)
                    if len(segment_vertices) < 2:
                        edge_exp.Next()
                        continue
                    
                    start_idx = vertex_offset
                    edge_vertices.extend(segment_vertices)
                    
                    for i in range(len(segment_vertices) - 1):
                        edge_indices.append([start_idx + i, start_idx + i + 1])
                    
                    vertex_offset = len(edge_vertices)
                    
                except Exception as exc:
                    warn_once(
                        "edge-discretization",
                        "Failed to discretize at least one STEP edge; skipping edge segment(s): %s",
                        exc,
                    )
                    continue
                
                edge_exp.Next()
            
            if len(edge_vertices) > 0 and len(edge_indices) > 0:
                solids_edges.append((
                    np.array(edge_vertices, dtype=np.float64),
                    np.array(edge_indices, dtype=np.int32)
                ))
            else:
                solids_edges.append((
                    np.array([], dtype=np.float64).reshape(0, 3),
                    np.array([], dtype=np.int32).reshape(0, 2)
                ))
            
            solid_explorer.Next()
        
        logger.info("Extracted edges for solids=%s", solid_count)
        return solids_edges

    @staticmethod
    def edge_segments(edge_vertices, edge_indices):
        """Flatten indexed native B-Rep edges into XYZ segment pairs for glTF extras."""
        if len(edge_vertices) == 0 or len(edge_indices) == 0:
            return []
        return np.asarray(edge_vertices, dtype=np.float64)[
            np.asarray(edge_indices, dtype=np.int64).reshape(-1)
        ].reshape(-1).tolist()

    def save_scene_as_glb(self, solids, output_path, sidecar_path=None, solids_edges=None):
        """
        Build a GLB from a list of (vertices, faces) solids, applying colors
        from a sidecar `.colors.json` if present.
        """
        scene = trimesh.Scene()

        side_objects = []
        if sidecar_path is not None and Path(sidecar_path).exists():
            try:
                with open(sidecar_path, 'r', encoding='utf-8') as sf:
                    data = json.load(sf)
                    side_objects = data.get('objects', [])
            except Exception as exc:
                warn_once(
                    "sidecar-read",
                    "Failed to parse sidecar colors JSON %s; continuing with defaults: %s",
                    sidecar_path,
                    exc,
                )
                side_objects = []

        # Precompute object centers
        obj_centers = []
        for o in side_objects:
            c = o.get('bbox_center') if isinstance(o.get('bbox_center'), (list, tuple)) else None
            obj_centers.append(np.array(c) if c is not None else None)

        unassigned = list(range(len(side_objects)))

        default_color = np.array([200, 200, 200, 255], dtype=np.uint8)

        for i, (verts, faces) in enumerate(solids):
            mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
            name = f'object_{i}'
            rgba = default_color

            if side_objects and len(unassigned) > 0:
                # match by nearest bbox center
                centroid = mesh.vertices.mean(axis=0)
                best_j = None
                best_d = float('inf')
                for j in unassigned:
                    c = obj_centers[j]
                    if c is None:
                        continue
                    d = np.linalg.norm(centroid - c)
                    if d < best_d:
                        best_d = d
                        best_j = j

                if best_j is None:
                    best_j = unassigned[0]

                matched = side_objects[best_j]
                try:
                    r, g, b = matched.get('color', [0.7, 0.7, 0.7])
                    a = matched.get('alpha', 1.0)
                    rgba = np.array([int(r * 255), int(g * 255), int(b * 255), int(a * 255)], dtype=np.uint8)
                    name = matched.get('label', name)
                except Exception as exc:
                    warn_once(
                        "sidecar-color",
                        "Failed to apply sidecar color metadata for object %s; using default color: %s",
                        i,
                        exc,
                    )
                    rgba = default_color

                unassigned.remove(best_j)

            # apply vertex colors
            try:
                vcols = np.tile(rgba, (len(mesh.vertices), 1))
                mesh.visual.vertex_colors = vcols
            except Exception as exc:
                warn_once(
                    "vertex-color",
                    "Failed to apply vertex colors for at least one object: %s",
                    exc,
                )

            # sanitize name
            try:
                safe_name = ''.join(ch if (ch.isalnum() or ch in (' ', '_', '-')) else '_' for ch in str(name))
            except Exception as exc:
                warn_once(
                    "name-sanitize",
                    "Failed to sanitize at least one mesh name; using fallback names: %s",
                    exc,
                )
                safe_name = f'object_{i}'

            metadata = {"partId": safe_name}
            if solids_edges and i < len(solids_edges):
                edge_verts, edge_indices = solids_edges[i]
                edge_segments = self.edge_segments(edge_verts, edge_indices)
                if edge_segments:
                    metadata["edgeSegments"] = edge_segments
            mesh.metadata["extras"] = {"modelryx": metadata}

            scene.add_geometry(mesh, node_name=safe_name, metadata={"modelryx": metadata})

        # Export scene to GLB
        export_double_sided_glb(scene, output_path)

        total_vertices = sum(len(g.vertices) for g in scene.geometry.values())
        total_faces = sum(len(g.faces) for g in scene.geometry.values())
        logger.info(
            "Saved GLB output=%s vertices=%s faces=%s",
            output_path,
            total_vertices,
            total_faces,
        )
    
    def save_as_glb(self, vertices, faces, output_path, validate_quality=True):
        """
        Save mesh data as GLB file using trimesh.
        
        Args:
            vertices (np.ndarray): Vertex array (N x 3).
            faces (np.ndarray): Face array (M x 3).
            output_path (str): Path for the output GLB file.
            validate_quality (bool): Perform quality checks and report issues.
        """
        logger.info("Creating GLB file: %s", output_path)
        
        # Create trimesh object with process=True to auto-clean
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
        
        # Quality validation
        if validate_quality:
            # Check for degenerate faces
            if hasattr(mesh, 'nondegenerate_faces'):
                valid_faces = mesh.nondegenerate_faces()
                degenerate_count = (~valid_faces).sum()
                if degenerate_count > 0:
                    logger.warning("Removed degenerate faces count=%s", degenerate_count)
            
            # Check for watertightness (closed solid)
            if not mesh.is_watertight:
                logger.warning("Mesh is not watertight and may have holes or gaps")
            
            # Check for inverted faces
            if hasattr(mesh, 'is_winding_consistent'):
                if not mesh.is_winding_consistent:
                    logger.warning("Inconsistent face winding detected; fixing normals")
                    mesh.fix_normals()
        
        # Ensure normals are computed correctly
        mesh.fix_normals()
        
        # Export as GLB
        export_double_sided_glb(mesh, output_path)
        
        logger.info(
            "Saved GLB output=%s vertices=%s faces=%s",
            output_path,
            len(vertices),
            len(faces),
        )
        
        # Report mesh quality
        if validate_quality:
            bbox = mesh.bounds
            size = bbox[1] - bbox[0]
            logger.info("Bounding box %.2f x %.2f x %.2f", size[0], size[1], size[2])
            if mesh.is_watertight:
                logger.info("Volume %.2f", mesh.volume)
                logger.info("Surface area %.2f", mesh.area)

    def save_step_colors_as_glb(self, step_file_path, output_path):
        """Preserve STEP surface styles per face, with solid and document color fallback."""
        from OCC.Core.Quantity import Quantity_Color
        from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
        from OCC.Core.TDF import TDF_LabelSequence
        from OCC.Core.TDocStd import TDocStd_Document
        from OCC.Core.TopAbs import TopAbs_SOLID
        from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

        document = TDocStd_Document("Modelryx STEP colors")
        shape_tool = XCAFDoc_DocumentTool.ShapeTool(document.Main())
        color_tool = XCAFDoc_DocumentTool.ColorTool(document.Main())
        reader = STEPCAFControl_Reader()
        reader.SetColorMode(True)
        if reader.ReadFile(str(step_file_path)) != IFSelect_RetDone or not reader.Transfer(document):
            raise ValueError("Unable to read STEP surface colors")
        labels = TDF_LabelSequence()
        shape_tool.GetFreeShapes(labels)
        if labels.Length() != 1:
            logger.info("STEP color import requires one root; using ordinary geometry conversion")
            return False
        root = shape_tool.GetShape(labels.Value(1))

        def color_of(shape):
            color = Quantity_Color()
            for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                if color_tool.GetColor(shape, kind, color):
                    return np.array([color.Red(), color.Green(), color.Blue(), 1.0])
            return None

        default = np.array([0.78, 0.78, 0.78, 1.0])
        root_color = color_of(root)
        solid_colors = {}
        solids = TopExp_Explorer(root, TopAbs_SOLID)
        while solids.More():
            solid = solids.Current()
            solid_color = color_of(solid)
            if solid_color is not None:
                faces = TopExp_Explorer(solid, TopAbs_FACE)
                while faces.More():
                    solid_colors[faces.Current()] = solid_color
                    faces.Next()
            solids.Next()
        vertex_parts, triangle_parts, color_parts = [], [], []
        offset = 0
        colored = root_color is not None
        face_explorer = TopExp_Explorer(root, TopAbs_FACE)
        while face_explorer.More():
            face = face_explorer.Current()
            color = color_of(face)
            if color is None:
                color = solid_colors.get(face)
            if color is None:
                color = root_color
            if color is not None:
                colored = True
            vertices, triangles = self.tessellate_shape(face)
            vertex_parts.append(vertices)
            triangle_parts.append(triangles + offset)
            color_parts.append(np.tile(np.rint(np.clip(color if color is not None else default, 0, 1) * 255).astype(np.uint8), (len(vertices), 1)))
            offset += len(vertices)
            face_explorer.Next()
        if not colored or not vertex_parts:
            return False
        mesh = trimesh.Trimesh(vertices=np.concatenate(vertex_parts), faces=np.concatenate(triangle_parts), process=False)
        mesh.visual.vertex_colors = np.concatenate(color_parts)
        metadata = {"partId": "step-model"}
        if self.export_edges:
            edge_vertices, edge_indices = self.extract_edges(root)
            metadata["edgeSegments"] = self.edge_segments(edge_vertices, edge_indices)
        mesh.metadata["extras"] = {"modelryx": metadata}
        scene = trimesh.Scene()
        scene.add_geometry(mesh, node_name="mesh", metadata={"modelryx": metadata})
        export_double_sided_glb(scene, output_path)
        return True
    
    def convert(self, step_file_path, output_glb_path=None):
        """
        Convert a STEP file to GLB format.
        
        Args:
            step_file_path (str): Path to the input STEP file.
            output_glb_path (str, optional): Path for the output GLB file.
                                            If None, uses the same name as input with .glb extension.
        
        Returns:
            str: Path to the created GLB file.
        """
        # Determine output path
        if output_glb_path is None:
            step_path = Path(step_file_path)
            output_glb_path = step_path.with_suffix('.glb')
        
        # Perform conversion
        shape = self.read_step_file(step_file_path)
        metadata_path = Path(output_glb_path).with_name("part-metadata.json")
        metadata_path.write_text(
            json.dumps(self.extract_metadata(shape, step_file_path), indent=2),
            encoding="utf-8",
        )

        # If a colors sidecar is present, tessellate per-solid and build a
        # GLB scene that preserves object colors/labels.
        sidecar_path = Path(step_file_path).with_suffix('.colors.json')
        if sidecar_path.exists():
            solids = self.tessellate_solids(shape)
            if solids and len(solids) > 0:
                # Extract edges per solid if edge export is enabled
                solids_edges = None
                if self.export_edges:
                    solids_edges = self.extract_edges_per_solid(shape)
                
                self.save_scene_as_glb(solids, str(output_glb_path), 
                                      sidecar_path=str(sidecar_path),
                                      solids_edges=solids_edges)
                return str(output_glb_path)

        try:
            if self.save_step_colors_as_glb(step_file_path, output_glb_path):
                logger.info("Saved GLB with STEP face and body colors: %s", output_glb_path)
                return str(output_glb_path)
        except Exception:
            logger.exception("STEP color extraction failed; continuing with uncolored geometry")

        # Fallback: single mesh export
        vertices, faces = self.tessellate_shape(shape)
        
        # Extract edges if enabled
        if self.export_edges:
            edge_verts, edge_indices = self.extract_edges(shape)
            if len(edge_verts) > 0:
                # Create a scene with mesh and edges
                scene = trimesh.Scene()
                mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
                metadata = {
                    "partId": "step-model",
                    "edgeSegments": self.edge_segments(edge_verts, edge_indices),
                }
                mesh.metadata["extras"] = {"modelryx": metadata}
                scene.add_geometry(
                    mesh, node_name='mesh', metadata={"modelryx": metadata}
                )
                export_double_sided_glb(scene, output_glb_path)
                logger.info("Saved GLB with native edge metadata: %s", output_glb_path)
                return str(output_glb_path)
        
        # Export without edges
        self.save_as_glb(vertices, faces, str(output_glb_path))

        return str(output_glb_path)


def main():
    """Command-line interface for STEP to GLB conversion."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    
    try:
        converter = StepToGlbConverter(
            tessellation_quality=args.quality,
            use_angular_deflection=not args.no_angular,
            export_edges=not args.no_edges
        )
        output_path = converter.convert(args.input, args.output)
        logger.info("Conversion successful output=%s", output_path)
        
        # Run validation if requested
        if args.validate:
            logger.info("Running quality validation")
            run_validation(output_path, args.input, logger)
        
        return 0
    
    except Exception as e:
        logger.exception("Conversion failed: %s", e)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
