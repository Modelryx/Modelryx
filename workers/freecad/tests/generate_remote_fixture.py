"""Generate a linked assembly for the opt-in standalone worker publication test."""

# ruff: noqa: I001
# FreeCAD rewrites module globals while initializing.
import FreeCAD  # type: ignore
import Part  # type: ignore

import sys
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
component = FreeCAD.newDocument("RemoteChild")
pin = component.addObject("PartDesign::Feature", "Pin")
pin.Shape = Part.makeCylinder(5, 12)
component.recompute()
component.saveAs(str(directory / "Child.FCStd"))
root = FreeCAD.newDocument("RemoteRoot")
root.saveAs(str(directory / "Root.FCStd"))
assembly = root.addObject("App::Part", "Assembly")
link = root.addObject("App::Link", "ExternalPin")
link.LinkedObject = pin
assembly.addObject(link)
root.recompute()
root.saveAs(str(directory / "Root.FCStd"))
FreeCAD.closeDocument(root.Name)
FreeCAD.closeDocument(component.Name)
