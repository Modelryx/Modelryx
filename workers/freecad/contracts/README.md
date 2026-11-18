# Worker-owned contract snapshots

This external worker owns a release snapshot of the Modelryx worker SDK (Apache-2.0 license
included under `vendor/worker-sdk`) and PartMetadata v1 JSON Schema. It does not import the
platform server or build against live `worker-sdk/` source. `snapshots.json` records source
locations, versions and exact SHA-256 values. Worker-local attributes preserve those bytes.

Platform and SDK changes do not alter an existing FreeCAD release. Adopt a contract update
explicitly from the worker directory:

```powershell
python scripts/refresh-contracts.py --platform-root ../..
```

Review the resulting snapshot diff and protocol/schema versions, run unit and native fixtures,
and qualify compatibility against the target Modelryx server. The update changes this worker's
folder and therefore triggers FreeCAD CI. Do not refresh snapshots automatically in CI or at
runtime. The schema validates native output in the worker's fixture suite; platform publication
and CAD plan selection are tested in the platform repository, not in the FreeCAD image.
