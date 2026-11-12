from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from fcstd_visibility import read_saved_visibility


def test_reads_only_unambiguous_saved_boolean_visibility(tmp_path: Path) -> None:
    path = tmp_path / "model.FCStd"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "GuiDocument.xml",
            """<Document><ViewProviderData>
            <ViewProvider name="Visible"><Properties><Property name="Visibility"><Bool value="true"/></Property></Properties></ViewProvider>
            <ViewProvider name="Hidden"><Properties><Property name="Visibility"><Bool value="false"/></Property></Properties></ViewProvider>
            <ViewProvider name="Bad"><Properties><Property name="Visibility"><Bool value="unknown"/></Property></Properties></ViewProvider>
            <ViewProvider name="Duplicate"><Properties><Property name="Visibility"><Bool value="true"/></Property></Properties></ViewProvider>
            <ViewProvider name="Duplicate"><Properties><Property name="Visibility"><Bool value="false"/></Property></Properties></ViewProvider>
            </ViewProviderData></Document>""",
        )
    assert read_saved_visibility(path) == {"Visible": True, "Hidden": False}


def test_missing_or_malformed_gui_document_has_unknown_visibility(tmp_path: Path) -> None:
    path = tmp_path / "model.FCStd"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Document.xml", "<Document/>")
    assert read_saved_visibility(path) == {}
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("GuiDocument.xml", "<bad")
    assert read_saved_visibility(path) == {}
