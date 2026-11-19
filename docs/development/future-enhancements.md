# Future enhancements

## CAD text and annotations in 3D previews

**Status: planned; not supported by the current FreeCAD 3D preview.**

Add CAD-neutral support for displaying text and annotations in the 3D viewer, and
implement extraction for the external FreeCAD worker. Some FreeCAD annotations do
not contain a CAD shape, so the current geometry exporter omits them from the 3D
preview. The native file remains available for viewing in FreeCAD. TechDraw PDF
export is a separate feature and does not add annotations to the 3D preview.

The enhancement should cover:

- FreeCAD annotation labels (`App::Annotation`), Draft text and labels, and Draft
  linear and angular dimensions, including their lines, arrows and displayed values.
- Text content and formatting, placement and orientation, visibility, scale,
  color, alignment, and parent and child relationships, using native information
  where available. Do not invent missing values or silently display unsupported
  annotations as accurate.
- A CAD-neutral worker output and viewer representation that supports selection,
  visibility controls and fit-to-view without tying the platform to FreeCAD.
- Diagnostics for annotations that cannot be represented faithfully, while
  retaining the rest of the preview.
- Tests for text, multiline labels, dimensions, placements in different planes,
  hidden items, nested groups, and viewer selection and fit. Include a FreeCAD
  fixture containing Draft objects and annotations.

Rendering fidelity and supported fonts should be documented as part of the
implementation. This enhancement has no committed release date.
