# FreeCAD converter

`fcstd_to_modelryx_glb.py` converts an FCStd document directly into a Modelryx Graphics
(`.pfg`) ZIP containing `manifest.json`, `model.glb`, `model-preview.json`, `diagnostics.json`
and drawing resources when produced.
`model-preview.json` contains the minimal presentation and hierarchy fields required for selection
and navigation-tree behavior. The worker emits `.freecad-raw-metadata.json` and
`.part-metadata.json` as adjacent sidecars for server persistence and AI indexing; these files are
not included in the preview package.

The maintained converter is shared by embedded processing and standalone FreeCAD jobs. It runs
inside a FreeCAD runtime with MeshPart, NumPy, trimesh and Pillow; native process supervision
lives in the worker adapter. Generic standalone jobs expose GLB/navigation roles derived from
the same PFG archive. See [the worker README](../README.md).

Run it inside the worker image:

```sh
python /opt/modelryx-converter/fcstd_to_modelryx_glb.py input.FCStd -o output.pfg
```

The standalone image installs the converter at `/app/converter/fcstd_to_modelryx_glb.py`;
`/opt/modelryx-converter` is the embedded image's path. Neither command starts the Modelryx API.
