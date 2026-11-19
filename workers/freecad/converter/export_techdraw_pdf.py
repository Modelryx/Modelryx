#!/usr/bin/env python3
"""Export every TechDraw page using FreeCAD's native GUI renderer."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# GUI rendering is isolated in this subprocess; the API itself stays headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import FreeCAD
import FreeCADGui
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from modelryx_freecad_shared import wait_for_techdraw_tasks


def main(source: Path, output: Path) -> None:
    FreeCADGui.showMainWindow()
    import TechDrawGui

    document = FreeCAD.openDocument(str(source))
    document.recompute()
    wait_for_techdraw_tasks(document)
    pages = [obj for obj in document.Objects if obj.TypeId == "TechDraw::DrawPage"]
    if len(pages) > 100:
        raise ValueError("FreeCAD drawing contains more than 100 pages")
    if not pages:
        return
    with TemporaryDirectory(dir=output.parent, prefix="techdraw-pdf-") as directory:
        writer = PdfWriter()
        for index, page in enumerate(pages):
            page_path = Path(directory) / f"page-{index:04}.pdf"
            TechDrawGui.exportPageAsPdf(page, str(page_path))
            if not page_path.is_file() or not page_path.stat().st_size:
                raise RuntimeError(f"Could not export TechDraw page {page.Name}")
            reader = PdfReader(str(page_path))
            if len(reader.pages) != 1:
                raise RuntimeError(f"Unexpected PDF output for TechDraw page {page.Name}")
            writer.add_page(reader.pages[0])
        with output.open("wb") as stream:
            writer.write(stream)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
    # FreeCAD GUI teardown can crash after successful offscreen export when
    # embedded in Python. The isolated process has no state to preserve.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
