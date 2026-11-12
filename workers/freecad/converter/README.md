# FreeCAD converter

`fcstd_to_modelryx_glb.py` converts an FCStd document directly into a Modelryx Graphics
(`.pfg`) ZIP containing only `manifest.json`, `model.glb`, and `model-preview.json`.
`model-preview.json` contains the minimal presentation and hierarchy fields required for selection
and navigation-tree behavior. The worker emits `.freecad-raw-metadata.json` and
`.part-metadata.json` as adjacent sidecars for server persistence and AI indexing; these files are
not included in the preview package.

The file was vendored from the adjacent Modelryx `step_to_glb` project and adjusted only to
remove its Windows-specific Conda path injection. The worker image supplies FreeCAD, MeshPart,
NumPy, and Trimesh through its Linux Conda environment.

Run it inside the worker image:

```sh
python /opt/modelryx-converter/fcstd_to_modelryx_glb.py input.FCStd -o output.pfg
```
