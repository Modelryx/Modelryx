> Historical design plan, not the current runtime contract. See the running OpenAPI schema
> and [current API guide](../docs/development/api.md) for implemented behavior.

# Edit Item Revision: Assembly Reconciliation Plan

## Purpose

The current Edit Item Revision flow can replace an assembly CAD artifact while retaining the old BOM. This can make the CAD reference graph and Modelryx assembly structure disagree.

The revised flow must treat the analyzed CAD reference graph as a candidate structure, reconcile it with the current BOM, require explicit resolution of every material difference, and commit the root, edited children, new Items, artifacts, and BOM atomically.

## Target workflow

```text
Checkout root revision
        |
Select root CAD file and complete folder/package
        |
Analyze references recursively
        |
Compare analyzed graph with current BOM snapshot
        |
Classify every occurrence
  Unchanged / Modified / Added / Removed / Relinked / Missing / Conflict
        |
Resolve files, identities, duplicates, and required checkouts
        |
Validate the complete proposed structure
        |
Review changes
        |
Commit all participants atomically
```

## Design principles

1. Never commit a new assembly file with an old, unverified BOM.
2. A parent relationship change does not require checkout of an unchanged child.
3. Changing a child's own CAD content requires checkout of that child revision.
4. New references must be resolved to an existing Item Revision or a proposed new Item.
5. Missing, ambiguous, conflicting, or stale references block commit.
6. File analysis is immutable for a staging snapshot. Any file change invalidates it.
7. The server generates the final BOM from the reconciled analysis; the browser must not construct an authoritative BOM independently.
8. Root and participating child changes commit all-or-nothing.

## 1. Start the edit session

When Edit Item Revision opens:

- Acquire or resume the checkout for the root revision.
- Record the exact base content snapshot and BOM snapshot.
- Load the current assembly tree and retained artifacts.
- Preserve the existing CAD package until the edit commits.
- Resume an existing edit session instead of creating a duplicate.
- Reject editing when the revision is immutable, another user owns its checkout, its base snapshot is stale, or it is no longer the current revision.

Child components are not checked out automatically. Checkout is required only when the child's own content will change.

## 2. Select complete CAD input

For an assembly, replace **Add CAD file** with **Select assembly file or folder**.

Supported inputs should include:

- An Inventor `.iam` root plus referenced `.ipt` and nested `.iam` files.
- A folder containing the root and its dependencies.
- A packaged assembly archive.
- A self-contained FreeCAD assembly.
- A single supported CAD file when editing a part.

The page must show the selected root, root-relative logical paths, included files, missing or external references, and unsupported reference types.

Selecting input creates a candidate package. It must not immediately replace the committed primary artifact.

## 3. Analyze references

The CAD worker should recursively extract:

- Parent and child logical paths.
- Persistent document and occurrence identifiers when available.
- Occurrence name and source reference.
- Document type: part, assembly, drawing, presentation, supporting file, or unknown.
- Transform, quantity, visibility, and suppression state.
- File hash and CAD document identity.
- Drawing-to-model ownership.
- Missing, external, or unsupported references.

The analysis result and input hashes must be saved against the staging snapshot. Changing any staged input invalidates the result and requires re-analysis.

## 4. Reconcile with the current BOM

Do not match references using filenames alone. Apply identity evidence in this order:

1. Persistent CAD occurrence or document identifier.
2. Existing source reference plus parent occurrence.
3. Embedded Modelryx Item or Revision identity.
4. Exact content hash.
5. Normalized logical path and filename.
6. Explicit user selection when the result remains ambiguous.

Each occurrence receives one reconciliation status:

| Status | Meaning | Required action |
|---|---|---|
| Unchanged | Same occurrence and referenced content | Reuse the existing Item Revision |
| Moved/Renamed | Same Item at a different path or occurrence name | Update relationship metadata |
| Modified | Same Item with different CAD content | Checkout and stage the child |
| Added | New reference absent from the old BOM | Resolve duplicate or create Item |
| Removed | Old occurrence absent from the new graph | Remove the BOM occurrence |
| Relinked | Occurrence points to a different existing Item | Confirm the new target |
| Missing | Required referenced file cannot be resolved | Block commit |
| Ambiguous | Multiple plausible Items match | Require user selection |
| Conflict | Required revision is unavailable | Block commit |

Comparison must be recursive for nested assemblies.

## 5. Scenario rules

### Unchanged reference

- Reuse the existing Item Revision without checkout.
- Reuse verified file content when hashes match.
- Display `Unchanged`.

### Deleted occurrence

- Display the occurrence as `Removed`.
- Remove only the parent-to-child relationship at commit.
- Do not delete the child Item, revision, or files.
- Do not require checkout of the removed child.
- Preserve other occurrences of the same child.
- Warn when the removal leaves the child unused, but never delete it automatically.

### Modified child content

- Display `Modified`.
- Require checkout of the child revision.
- Resume a checkout already owned by the current user.
- Block when another user owns the checkout.
- Require a new revision when the current child revision is immutable.
- Validate the child staging session independently.

Placement, quantity, suppression, visibility, and occurrence-name changes affect the parent relationship and do not require child checkout by themselves.

### New reference

- Hash and inspect the file.
- Run the standard duplicate-detection workflow.
- Allow linking an exact existing revision, selecting a likely match, or creating a new Item and Revision.
- Display `New` until identity resolution is complete.
- Analyze new child assemblies recursively.
- Block commit while any decision remains unresolved.

### Replaced reference

When an occurrence changes from Part A to Part B:

- Show Part A as removed from that occurrence.
- Show Part B as added or relinked.
- Do not require checkout of Part A.
- Require checkout of Part B only if Part B's own content changes.
- Require confirmation when identity evidence is uncertain.

### Renamed or moved file

- Treat it as `Moved/Renamed` when content hash or embedded identity proves it is the same Item.
- Update source-reference metadata without creating a duplicate Item.
- Ask the user when identity cannot be proven.

### Missing reference

- Show the expected path and referring assembly.
- Allow adding the missing file or correcting/removing the reference in CAD.
- Block commit.
- Never silently retain the old BOM relationship.

### Suppressed component

- Preserve the occurrence with `is_suppressed=true` by default.
- Apply view-specific policy for engineering and manufacturing BOMs.
- Do not interpret suppression as deletion.

### Drawing or presentation

- Attach it to its owning model or assembly.
- Do not create a BOM component for it.
- Require resolution when its owning model is missing or ambiguous.

### Shared component

- Show where-used impact before committing modified content.
- Require explicit confirmation for broad impact.
- Do not create copies solely because the child has multiple parents.

### Circular reference

- Detect cycles during analysis.
- Block commit and display the complete cycle path.

### Repeated child and duplicate occurrence names

- Permit multiple occurrences of the same child.
- Preserve transform and state per occurrence.
- Require a stable, unique occurrence key.
- Suggest unique keys when CAD data produces collisions.

### Checkout and lifecycle conflicts

Block affected content changes when a required revision is checked out by another user, immutable, no longer current, based on a stale snapshot, deleted, or superseded. Unchanged references remain usable without checkout.

## 6. Page design

### Assembly input

Show:

- Root assembly filename.
- Selected folder or package.
- Analyze/Re-analyze action.
- Analysis state and missing-file count.

### Structure changes

Display a tree such as:

```text
v ASM-0000051 Front Hub Carrier L       Root - Checked out
  |- PRT-0000102 Bearing                Unchanged
  |- ASM-0000055 Brake Assembly         Modified - Checkout required
  |  `- PRT-0000130 Pad                 New - Identity required
  |- PRT-0000110 Spacer                 Removed
  `- PRT-0000148 Seal                   Missing file
```

Provide filters for All, Changed, Requires action, Conflicts, New, and Removed.

A component checkbox acquires or releases checkout only. Clicking it must not expand or collapse the tree.

### Identity resolution

For every added or ambiguous document, show exact duplicates, possible matches, link-existing and create-new choices, proposed Item number, revision and Item type, and duplicate evidence.

### Action row and staged content

The action row contains Review/Submit and Cancel. Staged Content remains last, below this row.

Staged Content shows the root assembly first, followed by changed child files, new files, retained artifacts, removed artifacts, and files reused from verified storage. Every file identifies its owning Item and status.

### Review changes

Before commit, summarize:

- Added, removed, modified, moved, and relinked components.
- New Items and revisions.
- Existing Items being updated.
- Checkout conflicts and missing references.
- Where-used impact.
- Files uploaded and files reused.

## 7. Validation model

Validation is rerun whenever files, identity decisions, BOM decisions, or checkouts change.

### Blocking errors

- Missing referenced file.
- Unresolved or ambiguous Item identity.
- Required checkout unavailable.
- Stale base content or BOM snapshot.
- Invalid or cyclic BOM.
- Duplicate occurrence keys.
- Missing, deleted, or superseded referenced revision.
- Unsupported CAD reference.
- Staged file changed after analysis.
- Analysis hashes differ from staged hashes.
- Proposed BOM differs from the reconciled CAD graph.

### Warnings requiring acknowledgment

- Removed component remains used elsewhere.
- Large where-used impact.
- Rename inferred only from hash.
- Suppressed component added or removed.
- Selected referenced revision is not current.
- CAD identity and user-selected identity differ.

### Informational changes

- Placement, quantity, or visibility changed.
- Verified file reused.
- Derived previews will be regenerated.

## 8. Atomic commit

The commit operation must:

1. Lock the root and all participating child staging sessions.
2. Recheck checkout ownership, lifecycle state, and base snapshots.
3. Verify staged hashes against the saved analysis.
4. Confirm every added file has an identity decision.
5. Confirm the proposed BOM exactly represents the reconciled graph.
6. Create approved new Items and revisions.
7. Commit modified child revisions.
8. Create the root content snapshot and BOM snapshot.
9. Exclude removed relationships from the new BOM snapshot.
10. Queue preview and metadata processing.
11. Complete all participating staging sessions.
12. Release checkouts according to policy.

Any failure rolls back all database changes and leaves the staging session resumable. Uploaded blobs may remain in staging for retry or scheduled cleanup.

## 9. Backend model and API changes

Prefer explicit persisted models over an unstructured proposal:

- `assembly_analysis`
- `assembly_analysis_file`
- `assembly_analysis_occurrence`
- `assembly_reconciliation`
- `assembly_identity_decision`
- `assembly_checkout_requirement`

Suggested endpoints:

- `POST /staging/{id}/assembly-package`
- `POST /staging/{id}/analyze`
- `GET /staging/{id}/reconciliation`
- `PUT /staging/{id}/identity-decisions/{node}`
- `POST /staging/{id}/component-checkouts/{revision}`
- `DELETE /staging/{id}/component-checkouts/{revision}`
- `POST /staging/{id}/validate`
- `POST /uploads/{id}/commit`

Reuse the Create Item dependency analyzer and duplicate-resolution services where possible, but keep the server authoritative for reconciliation and final BOM generation.

## 10. Delivery phases

### Phase 1: Safety guard

- [x] Prevent root assembly CAD replacement without dependency analysis in the UI.
- [x] Enforce the same protection in backend readiness checks.
- [x] Block assembly commit when a current persisted analysis is unavailable.
- [x] Block when the analyzed graph and proposed BOM differ.
- Keep existing single-part editing available.

This is the highest-priority phase because it prevents additional inconsistent product data.

### Phase 2: Folder and package analysis

- [x] Reuse the existing server-side dependency analyzer for folder selection.
- [x] Accept a root file plus dependency files for analysis.
- [x] Persist the analyzed graph and an exact file hash manifest against the staging snapshot.
- [x] Report missing references and analyzer validation issues in Edit Item Revision.
- [x] Support the local Inventor/FreeCAD agent package path in Edit Item Revision.
- [x] Stage the analyzed package after reconciliation decisions are complete.

### Phase 3: BOM reconciliation

- [x] Compare analyzed root occurrences with the staged base BOM using stable occurrence keys.
- [x] Classify unchanged, modified, added, removed, moved/renamed, relinked, missing, and conflicting occurrences.
- [x] Compare analyzed file hashes with verified files on the currently linked child revision.
- [x] Persist and display the reconciliation result.
- [x] Reconcile matched nested child assemblies against their own latest BOM snapshots.
- [x] Apply safe root removals, moves/renames, and relationship-property changes to the proposal.
- [x] Apply safe nested removals and relationship metadata through coordinated child staging sessions.
- [x] Apply nested added/relinked identities and files through coordinated child staging sessions.
- [x] Package descendants of a newly created nested assembly into that new assembly's own Artifact.
- [x] Apply added and relinked references after Phase 4 identity decisions.

### Phase 4: Identity and duplicate resolution

- [x] Reuse exact primary-file hash matching from the Create Item duplicate workflow.
- [x] Show verified existing-revision candidates for added and relinked occurrences.
- [x] Persist an identity decision per occurrence.
- [x] Support selecting an existing revision or generating a proposed new Item and Revision.
- [x] Detect moves and renames through occurrence and content identity evidence.
- [x] Stage analyzed files after all root identity decisions are resolved.
- [x] Translate root decisions into proposal identities, Artifacts, and BOM lines.
- [x] Translate nested changes through coordinated child edit sessions.

### Phase 5: Coordinated checkout and atomic commit

- [x] Create newly referenced component Items and revisions in the root edit transaction.
- [x] Create their authoritative Artifacts and initial content snapshots atomically.
- [x] Resolve staged BOM children to newly created or reused revisions.
- [x] Queue processing for new component revisions together with the root.
- [x] Preserve rollback semantics for root, new Items, Artifacts, snapshots, and BOM.
- [x] Calculate modified-child checkout requirements from reconciliation.
- [x] Acquire each required child checkout once and stage its analyzed CAD file.
- [x] Apply nested changes through coordinated child staging sessions.
- [x] Retain stale-snapshot and checkout verification at commit time.

### Phase 6: UX and recovery

- [x] Add the review screen, tree filters, and clear status explanations.
- [x] Resume incomplete reconciliation safely, preserving server-side analysis and child staging while requiring local package re-selection only when bytes are still needed.
- [x] Add where-used warnings.
- [x] Support retrying failed post-commit processing without repeating the commit.

## 11. Test matrix

Automated tests should cover at least:

- Root file changed with identical references.
- One occurrence removed.
- One of several repeated occurrences removed.
- Existing component replaced with another existing Item.
- Existing child content modified with and without checkout.
- Child checked out by another user.
- New unique part.
- New part matching an exact duplicate.
- Ambiguous duplicate.
- New child assembly with nested children.
- Missing direct or nested reference.
- Renamed file with identical hash.
- Same filename with different content.
- Suppressed component.
- Placement-only, quantity, and visibility changes.
- Drawing and presentation references.
- Circular assembly.
- Duplicate occurrence keys.
- Released or superseded child revision.
- Root or child snapshot changed during editing.
- Analysis invalidated after a staged file changes.
- Validation failure after some children become ready.
- Commit failure followed by safe retry.
- Two users editing overlapping assemblies.
- Successful atomic root-and-child commit.
- Processing failure after successful commit.

## Immediate recommendation

Implement Phase 1 before extending the editor. Until the safety guard exists, replacing an assembly CAD file can preserve a stale BOM and create inconsistent product data.
