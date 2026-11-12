import { Fragment } from "react";
import { Link } from "react-router-dom";
import { flattenEditOccurrences, normalizedCadReference, retainedFileOccurrence } from "./model";
import type { CheckinController } from "./useCheckinWorkflowController";

export function EditRevisionView({ controller }: { controller: CheckinController }) {
  const {
    targetItemId,
    targetItemNumber,
    targetItemName,
    targetRevision,
    dependencyReport,
    workerAnalysisFiles,
    cadSelectionBusy,
    editStaging,
    editStagingError,
    editBusy,
    editMessage,
    committedItemId,
    committedRevisionId,
    commitProcessingJobs,
    commitProcessingLoaded,
    commitProcessingError,
    editWhereUsed,
    editContentMenu,
    setEditContentMenu,
    editOccurrences,
    collapsedEditNodes,
    editReconciliationFilter,
    setEditReconciliationFilter,
    showEditReview,
    setShowEditReview,
    childEditSessions,
    editOccurrenceMenu,
    setEditOccurrenceMenu,
    toggleEditNode,
    visibleEditOccurrences,
    editAssemblyAnalysis,
    editReconciliation,
    editAnalysisRows,
    editAnalysisIsCurrent,
    unresolvedEditIdentities,
    modifiedEditReferences,
    filteredEditReconciliation,
    editReconciliationCounts,
    unpreparedModifiedReferences,
    editBlockingCount,
    editCreateNewCount,
    editReuseCount,
    editAssemblyReviewBlocked,
    checkoutChild,
    cancelChildCheckout,
    addEditFile,
    checkinChildFile,
    downloadEditFile,
    submitEdit,
    retryCommitProcessing,
    cancelEdit,
    importEditCadPackage,
    applySafeEditReconciliation,
    resolveEditAssemblyIdentity,
    stageResolvedEditAssembly,
    prepareModifiedEditChildren,
    embedded,
  } = controller;

  return (
    <section className={`page checkin-new-page${embedded ? " checkin-new-page--embedded" : ""}`}>
      <div className="checkin-header">
        <div>
          {embedded ? <h2>Edit Item Revision</h2> : <h1>Edit Item Revision</h1>}
          <p className="page-subtitle">
            Prepare changes against a fixed content snapshot, then submit them together.
          </p>
        </div>
      </div>
      <div className="target-revision-banner">
        <div>
          <strong>{targetItemNumber}</strong>
          <span>{targetItemName} · Revision {targetRevision}</span>
        </div>
        {targetItemId && !embedded && <Link to={`/items/${targetItemId}`}>Back to item</Link>}
      </div>
      {editStagingError && <p className="error">{editStagingError}</p>}
      {editMessage && <p className="success">{editMessage}</p>}
      {!editStaging && !editStagingError && <p>Creating the staging session and checkout…</p>}
      {editStaging && (
        <section className="checkin-plan-panel">
          <section className="edit-staged-content">
          <h2>Staged content</h2>
          <p className="muted">
            Checkout acquired · Base snapshot {editStaging.base_snapshot_id}
          </p>
          {(editStaging.proposal?.artifacts?.length ?? 0) === 0 ? (
            <p>No Artifact relations are present on this revision.</p>
          ) : (
            <div className="edit-content-table-wrap">
              <table className="edit-content-table">
                <thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Status</th></tr></thead>
                <tbody>
                  {editStaging.proposal.artifacts.map((artifact: any) => {
                    const retained = editStaging.retained_artifacts
                      ?.find((entry) => artifact.existing_relation_id
                        ? entry.relation_id === artifact.existing_relation_id
                        : entry.artifact_id === artifact.existing_artifact_id);
                    const files = retained?.files ?? (artifact.files ?? []).map((file: any) => {
                      const staged = editStaging.files.find(
                        (entry) => entry.id === file.staged_file_id,
                      );
                      return staged ? { ...staged, filename: staged.filename } : null;
                    }).filter(Boolean);
                    const rootIdentity = normalizedCadReference(
                      editStaging.proposal?.item?.name,
                    ).replace(/[^a-z0-9]+/g, "");
                    const fileIdentity = (file: any) => normalizedCadReference(file.filename)
                      .split("/").at(-1)?.replace(/\.[^.]+$/, "")
                      .replace(/[^a-z0-9]+/g, "");
                    const visibleFiles = files.filter((file: any) => {
                      if (!/\.(?:iam|ipt)$/i.test(file.filename)) return true;
                      return fileIdentity(file) === rootIdentity
                        || Boolean(retainedFileOccurrence(file, editOccurrences));
                    }).sort((left: any, right: any) => {
                      const leftRoot = fileIdentity(left) === rootIdentity;
                      const rightRoot = fileIdentity(right) === rootIdentity;
                      if (leftRoot !== rightRoot) return leftRoot ? -1 : 1;
                      return 0;
                    });
                    return [
                      <tr key={`artifact-${artifact.existing_relation_id ?? artifact.client_artifact_id}`}
                        className="edit-content-artifact-row"
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setEditContentMenu({ artifact, x: event.clientX, y: event.clientY });
                        }}>
                        <td><strong>{artifact.name}</strong></td>
                        <td>{artifact.artifact_type.replaceAll("_", " ")}</td>
                        <td>—</td>
                        <td>{artifact.existing_relation_id ? "Retained"
                          : artifact.existing_artifact_id ? "Linked from Item"
                            : artifact.attachment_id ? "New version" : "Added"}</td>
                      </tr>,
                      ...visibleFiles.map((file: any) => {
                        const occurrence = artifact.existing_relation_id
                          ? retainedFileOccurrence(file, editOccurrences) : undefined;
                        const occurrenceDepth = occurrence
                          ? flattenEditOccurrences(editOccurrences)
                            .find((entry) => entry.occurrence.id === occurrence.id)?.depth ?? 0
                          : -1;
                        const childStaging = occurrence
                          ? childEditSessions[occurrence.child_revision.id] : undefined;
                        const checkedOutByOther = occurrence?.child_revision.checkout_status
                          === "CHECKED_OUT_BY_OTHER";
                        const checkoutUnavailable = occurrence
                          ? checkedOutByOther || occurrence.child_revision.lifecycle_state !== "WORKING"
                          : false;
                        return (
                        <tr key={`file-${file.id ?? file.staged_file_id}`}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            setEditContentMenu({ artifact, file, x: event.clientX, y: event.clientY });
                          }}>
                          <td className="edit-content-child"
                            style={{ paddingLeft: `${1.4 + Math.max(0, occurrenceDepth) * 1.5}rem` }}>
                            ↳ {file.filename}
                          </td>
                          <td>{file.mime_type || "CAD reference"}</td>
                          <td>{Math.max(1, Math.ceil(file.byte_size / 1024))} KB</td>
                          <td>{occurrence ? (
                            <button type="button" disabled={editBusy || checkoutUnavailable || Boolean(childStaging)}
                              title={checkedOutByOther
                                ? "This part is checked out by another user"
                                : checkoutUnavailable ? "Only working revisions can be checked out" : undefined}
                              onClick={() => void checkoutChild(occurrence)}>
                              {childStaging ? "Checked out"
                                : occurrence.child_revision.checkout_status === "CHECKED_OUT_BY_ME"
                                  ? "Continue checkout" : "Check out"}
                            </button>
                          ) : artifact.existing_relation_id ? "Available" : "Checked in"}</td>
                        </tr>
                        );
                      }),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          )}
          {editContentMenu && (
            <nav className="edit-content-menu" style={{ left: editContentMenu.x, top: editContentMenu.y }}
              aria-label={`Actions for ${editContentMenu.file?.filename ?? editContentMenu.artifact.name}`}
              onClick={(event) => event.stopPropagation()}>
              {editContentMenu.file?.download_url && (
                <button type="button" disabled={editBusy} onClick={() => {
                  void downloadEditFile(editContentMenu.file);
                  setEditContentMenu(null);
                }}>Download</button>
              )}
            </nav>
          )}
          </section>
          {editStaging.proposal?.item?.item_type === "ASSEMBLY" && (
            <section>
              <h2>Assembly structure</h2>
              <p className="muted">Select a component to acquire its checkout. Availability is verified by the server when selected.</p>
              <div className="edit-content-table-wrap">
                  <table className="edit-content-table">
                    <thead><tr><th className="component-checkout-column"><span className="sr-only">Checkout</span></th><th>Part</th><th>Revision</th><th>Quantity</th><th>Status</th></tr></thead>
                    <tbody>
                      <tr className="component-root-row">
                        <td className="component-checkout-column">
                          <input type="checkbox" checked disabled
                            aria-label={`Current component ${editStaging.proposal.item.item_number} is checked out`} />
                        </td>
                        <td><button type="button" className="edit-tree-toggle"
                          aria-label={`${collapsedEditNodes.has("root") ? "Expand" : "Collapse"} root assembly`}
                          aria-expanded={!collapsedEditNodes.has("root")}
                          onClick={() => toggleEditNode("root")}>
                          {collapsedEditNodes.has("root") ? "▸" : "▾"}
                        </button><strong>{editStaging.proposal.item.item_number}</strong><span className="muted"> · {editStaging.proposal.item.name}</span></td>
                        <td>{editStaging.proposal.revision.revision_code}</td>
                        <td>1</td>
                        <td>Checked out by me</td>
                      </tr>
                      {!collapsedEditNodes.has("root") && visibleEditOccurrences(editOccurrences).map(({ occurrence, depth }) => {
                      const childStaging = childEditSessions[occurrence.child_revision.id];
                      const checkedOutByOther = occurrence.child_revision.checkout_status === "CHECKED_OUT_BY_OTHER";
                      const checkoutUnavailable = checkedOutByOther
                        || occurrence.child_revision.lifecycle_state !== "WORKING";
                      return <Fragment key={occurrence.id}>
                        <tr onContextMenu={(event) => {
                          event.preventDefault();
                          setEditOccurrenceMenu({ occurrence, x: event.clientX, y: event.clientY });
                        }}>
                          <td className="component-checkout-column">
                            <input type="checkbox"
                              checked={Boolean(childStaging)}
                              disabled={editBusy || checkoutUnavailable}
                              title={checkedOutByOther
                                ? "This component is checked out by another user"
                                : checkoutUnavailable ? "Only working revisions can be checked out" : undefined}
                              aria-label={`${childStaging ? "Release" : "Check out"} ${occurrence.child_item.item_number} revision ${occurrence.child_revision.revision_code}`}
                              onChange={(event) => {
                                if (event.target.checked) void checkoutChild(occurrence);
                                else void cancelChildCheckout(occurrence);
                              }} />
                          </td>
                          <td style={{ paddingLeft: `${0.8 + depth * 1.5}rem` }}>
                            {occurrence.child_item.item_type === "ASSEMBLY" && occurrence.children.length > 0 ? (
                              <button type="button" className="edit-tree-toggle"
                                aria-label={`${collapsedEditNodes.has(occurrence.id) ? "Expand" : "Collapse"} ${occurrence.child_item.name}`}
                                aria-expanded={!collapsedEditNodes.has(occurrence.id)}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  toggleEditNode(occurrence.id);
                                }}>
                                {collapsedEditNodes.has(occurrence.id) ? "▸" : "▾"}
                              </button>
                            ) : <span className="edit-tree-spacer" aria-hidden="true" />}
                            <strong>{occurrence.child_item.item_number}</strong>
                            <span className="muted"> · {occurrence.child_item.name}</span>
                          </td>
                          <td>{occurrence.child_revision.revision_code}</td>
                          <td>{occurrence.quantity}</td>
                          <td>{childStaging ? "Checked out by me" : occurrence.child_revision.checkout_status.replaceAll("_", " ")}</td>
                        </tr>
                      </Fragment>;
                    })}</tbody>
                  </table>
                </div>
            </section>
          )}
          {editOccurrenceMenu && (
            <nav className="edit-content-menu"
              style={{ left: editOccurrenceMenu.x, top: editOccurrenceMenu.y }}
              aria-label={`Actions for ${editOccurrenceMenu.occurrence.child_item.item_number}`}
              onClick={(event) => event.stopPropagation()}>
              {childEditSessions[editOccurrenceMenu.occurrence.child_revision.id] ? (
                <>
                  {childEditSessions[editOccurrenceMenu.occurrence.child_revision.id]
                    .retained_artifacts.flatMap((artifact) => artifact.files).map((file) => (
                      <button key={file.id} type="button" disabled={editBusy} onClick={() => {
                        void downloadEditFile(file);
                        setEditOccurrenceMenu(null);
                      }}>Download {file.filename}</button>
                    ))}
                  <label className="edit-content-menu-file">
                    Check in CAD file…
                    <input type="file" hidden disabled={editBusy}
                      accept=".FCStd,.fcstd,.dwg,.DWG,.dxf,.DXF,.ipt,.IPT,.iam,.IAM,.idw,.IDW,.step,.stp"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void checkinChildFile(editOccurrenceMenu.occurrence, file);
                        event.target.value = "";
                      }} />
                  </label>
                  <button type="button" disabled={editBusy}
                    onClick={() => void cancelChildCheckout(editOccurrenceMenu.occurrence)}>
                    Cancel checkout
                  </button>
                </>
              ) : editOccurrenceMenu.occurrence.child_revision.lifecycle_state === "WORKING"
                && editOccurrenceMenu.occurrence.child_revision.checkout_status !== "CHECKED_OUT_BY_OTHER" && (
                <button type="button" disabled={editBusy}
                  onClick={() => void checkoutChild(editOccurrenceMenu.occurrence)}>
                  Check out
                </button>
              )}
            </nav>
          )}
          <div className="checkin-actions">
            <label className="button">
              Add CAD file
              <input type="file" hidden disabled={editBusy} accept=".FCStd,.fcstd,.dwg,.DWG,.dxf,.DXF,.ipt,.IPT,.iam,.IAM,.idw,.IDW,.step,.stp" onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void addEditFile(file);
                event.target.value = "";
              }} />
            </label>
            <label className="button" title="Import a Modelryx Download all ZIP and verify its manifest.">
              Import CAD package
              <input type="file" hidden disabled={editBusy || cadSelectionBusy} accept=".zip,application/zip"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void importEditCadPackage(file);
                  event.target.value = "";
                }} />
            </label>
            <button type="button" className="button-primary"
              disabled={editBusy}
              onClick={() => setShowEditReview(true)}>
              Review changes
            </button>
            <button type="button" disabled={editBusy} onClick={() => void cancelEdit()}>
              Cancel
            </button>
          </div>
          {editStaging.proposal?.item?.item_type === "ASSEMBLY" && (
            <div className="validation-summary">
              {dependencyReport ? (
                <>
                  <strong>Assembly analysis saved for staging snapshot {editAssemblyAnalysis?.snapshot_version}</strong>
                  <p>
                    Root: {dependencyReport.root_logical_path} · {dependencyReport.files.length} required files · {(dependencyReport.references ?? []).length} references
                  </p>
                  {dependencyReport.missing_references.length > 0 && (
                    <div className="validation-warning">
                      <strong>{dependencyReport.missing_references.length} missing references</strong>
                      <ul>{dependencyReport.missing_references.map((missing, index) => (
                        <li key={`${missing.reference}-${index}`}>
                          {missing.reference} (referenced by {missing.referenced_by})
                        </li>
                      ))}</ul>
                    </div>
                  )}
                  {dependencyReport.validation_issues?.map((issue, index) => (
                    <p className={issue.severity === "ERROR" ? "error" : "muted"}
                      key={`${issue.code}-${index}`}>{issue.message}</p>
                  ))}
                  {!editAnalysisIsCurrent && (
                    <p className="error">
                      Staged content changed after this analysis. Analyze the assembly again before continuing.
                    </p>
                  )}
                  {editReconciliation.length > 0 && (
                    <div className="edit-reconciliation-section">
                      <div className="edit-reconciliation-toolbar">
                        <div className="edit-reconciliation-counts">
                          <span>{editAnalysisRows.length} files and references</span>
                          <span>{editAnalysisRows.length - (editReconciliationCounts.UNCHANGED ?? 0)} changed</span>
                          <span>{editReconciliationCounts.ADDED ?? 0} new</span>
                          <span>{editReconciliationCounts.REMOVED ?? 0} removed</span>
                          <span>{editBlockingCount} actions remaining</span>
                        </div>
                        <label>
                          Show
                          <select value={editReconciliationFilter}
                            onChange={(event) => setEditReconciliationFilter(event.target.value as typeof editReconciliationFilter)}>
                            <option value="ALL">All references</option>
                            <option value="CHANGED">Changed only</option>
                            <option value="ACTION">Action required</option>
                            <option value="CONFLICT">Missing or conflict</option>
                            <option value="NEW">New references</option>
                            <option value="REMOVED">Removed references</option>
                          </select>
                        </label>
                      </div>
                      <p className="muted edit-reconciliation-legend">
                        Modified means file content changed. Relinked means the occurrence now targets a different Item.
                        Moved/renamed preserves identity. Missing and conflict block commit.
                      </p>
                      <div className="edit-content-table-wrap">
                      <table className="edit-content-table">
                        <thead><tr><th>Occurrence</th><th>Previous reference</th><th>Analyzed reference</th><th>Status</th><th>Identity</th></tr></thead>
                        <tbody>{filteredEditReconciliation.map((entry, index) => {
                          const isRootFile = entry === editAssemblyAnalysis?.root_file_reconciliation;
                          const oldReference = String(entry.old?.source_reference
                            ?? entry.old?.filename ?? entry.old?.child_revision_id
                            ?? entry.old?.revision_id ?? "—");
                          const newReference = String(entry.new?.child_logical_path
                            ?? entry.new?.logical_path ?? entry.new?.reference ?? "—");
                          const decisionKey = `${String(entry.parent_logical_path ?? "").toLowerCase()}::${entry.occurrence_key.toLowerCase()}`;
                          const identity = editAssemblyAnalysis?.identity_decisions?.[decisionKey];
                          return <tr key={`${entry.occurrence_key}-${index}`}>
                            <td style={{ paddingLeft: `${0.8 + (entry.depth ?? 0) * 1.5}rem` }}>
                              <strong>{entry.occurrence_key || "Unidentified occurrence"}</strong>
                              {isRootFile ? <small className="muted"> root assembly</small> : null}
                              {(entry.depth ?? 0) > 0 && entry.parent_logical_path
                                ? <small className="muted"> under {entry.parent_logical_path}</small> : null}
                            </td>
                            <td>{oldReference}</td>
                            <td>{newReference}</td>
                            <td><span className={`status-badge ${entry.status.toLowerCase()}`}
                              title={entry.reason}>{entry.status.replaceAll("_", " ")}</span></td>
                            <td>{["ADDED", "RELINKED"].includes(entry.status) ? (
                              <select disabled={editBusy} value={identity?.decision === "CREATE_NEW"
                                ? "CREATE_NEW" : identity?.existing_revision_id ?? ""}
                                onChange={(event) => {
                                  if (event.target.value) void resolveEditAssemblyIdentity(entry, event.target.value);
                                }}>
                                <option value="">Resolve…</option>
                                {(entry.duplicate_candidates ?? []).map((candidate) => (
                                  <option key={candidate.revision_id} value={candidate.revision_id}>
                                    {candidate.item_number} Rev {candidate.revision_code} — {candidate.item_name}
                                  </option>
                                ))}
                                <option value="CREATE_NEW">Create new Item</option>
                              </select>
                            ) : "—"}</td>
                          </tr>;
                        })}</tbody>
                      </table>
                      {filteredEditReconciliation.length === 0 && (
                        <p className="muted edit-reconciliation-empty">No references match this filter.</p>
                      )}
                      </div>
                    </div>
                  )}
                  {editAnalysisIsCurrent && !editAssemblyAnalysis?.safe_changes_applied
                    && editReconciliation.some((entry) => entry.depth === 0
                      && ["REMOVED", "MOVED_RENAMED", "UNCHANGED"].includes(entry.status)) && (
                    <button type="button" disabled={editBusy
                      || editReconciliation.some((entry) => ["MISSING", "CONFLICT"].includes(entry.status))}
                      onClick={() => void applySafeEditReconciliation()}>
                      Apply safe structure changes
                    </button>
                  )}
                  {editAssemblyAnalysis?.safe_changes_applied && (
                    <p className="success">Safe root relationship changes are applied to the staged BOM.</p>
                  )}
                  {modifiedEditReferences.length > 0 && (
                    <>
                      <button type="button" disabled={editBusy || modifiedEditReferences.every((entry) =>
                        Boolean(childEditSessions[String(entry.old?.child_revision_id ?? "")]))
                        || !editAssemblyAnalysis?.package_staged || !editAnalysisIsCurrent}
                        onClick={() => void prepareModifiedEditChildren()}>
                        Prepare or restore {modifiedEditReferences.length} modified component{modifiedEditReferences.length === 1 ? "" : "s"}
                      </button>
                      {workerAnalysisFiles.length === 0 && unpreparedModifiedReferences.length > 0 && (
                        <p className="muted">
                          Saved child edits are restored when possible. Re-select the original assembly folder only when staged file bytes are still needed.
                        </p>
                      )}
                    </>
                  )}
                  {editAnalysisIsCurrent && unresolvedEditIdentities.length === 0
                    && !editReconciliation.some((entry) => ["MISSING", "CONFLICT"].includes(entry.status))
                    && !editAssemblyAnalysis?.package_staged && (
                    <>
                      <button type="button" className="button-primary"
                        disabled={editBusy}
                        onClick={() => void stageResolvedEditAssembly()}>
                        Stage resolved assembly package
                      </button>
                    </>
                  )}
                  {editAssemblyAnalysis?.package_staged && (
                    <p className="success">The analyzed package and reconciled root BOM are staged.</p>
                  )}
                  <p className="muted">
                    Reconciliation is saved for review. Added and relinked references must receive Item identity decisions before files and BOM changes can be applied.
                  </p>
                </>
              ) : (
                <p className="muted">
                  Select the complete assembly folder to analyze its dependency tree. Root replacement remains blocked until that tree is reconciled with the current BOM.
                </p>
              )}
            </div>
          )}
          {showEditReview && (
            <div className="edit-review-panel" role="region" aria-label="Review changes">
              <div className="edit-review-heading">
                <div>
                  <h3>Review changes</h3>
                  <p className="muted">
                    {editStaging.proposal?.item?.item_number} · Revision {editStaging.proposal?.revision?.revision_code}
                  </p>
                </div>
                <button type="button" disabled={editBusy} onClick={() => setShowEditReview(false)}>
                  Back to editing
                </button>
              </div>
              <div className="edit-review-summary">
                <span><strong>{editStaging.files.length}</strong> staged files</span>
                <span><strong>{editStaging.proposal?.artifacts?.length ?? 0}</strong> artifact groups</span>
                <span><strong>{editStaging.proposal?.artifacts?.filter((artifact: any) =>
                  artifact.relation_type === "ATTACHMENT").length ?? 0}</strong> Attachment</span>
                {editAssemblyAnalysis && (
                  <>
                    <span><strong>{editReconciliation.length - (editReconciliationCounts.UNCHANGED ?? 0)}</strong> structure changes</span>
                    <span><strong>{editCreateNewCount}</strong> new Items</span>
                    <span><strong>{editReuseCount}</strong> reused Items</span>
                    <span><strong>{Object.keys(childEditSessions).length}</strong> child revisions prepared</span>
                  </>
                )}
              </div>
              {editWhereUsed && editWhereUsed.items.length > 0 && (
                <div className="validation-warning">
                  <strong>Where-used impact</strong>
                  <p>
                    This revision is referenced by {editWhereUsed.items.length} parent assembly path{editWhereUsed.items.length === 1 ? "" : "s"}.
                    Existing parent revisions keep their saved BOM, but consumers may need review after this change.
                  </p>
                  <ul>{editWhereUsed.items.slice(0, 5).map((usage) => (
                    <li key={`${usage.parent_revision.id}-${usage.occurrence_key}`}>
                      {usage.parent_part.part_number} Rev {usage.parent_revision.revision_code}
                      {usage.direct ? " (direct)" : ` (${usage.depth} levels)`}
                    </li>
                  ))}</ul>
                  {editWhereUsed.items.length > 5 && <p className="muted">
                    And {editWhereUsed.items.length - 5} more parent paths.
                  </p>}
                  {editWhereUsed.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}
                </div>
              )}
              {editAssemblyReviewBlocked ? (
                <div className="validation-warning">
                  <strong>Commit is blocked</strong>
                  <ul>
                    {!editAnalysisIsCurrent && <li>The assembly analysis is stale.</li>}
                    {editAssemblyAnalysis && !editAssemblyAnalysis.package_staged
                      && <li>The reconciled assembly package has not been staged.</li>}
                    {unresolvedEditIdentities.length > 0
                      && <li>{unresolvedEditIdentities.length} new or relinked references need an identity decision.</li>}
                    {unpreparedModifiedReferences.length > 0
                      && <li>{unpreparedModifiedReferences.length} modified child references are not prepared.</li>}
                    {editReconciliation.some((entry) => ["MISSING", "CONFLICT"].includes(entry.status))
                      && <li>Missing or conflicting references must be resolved.</li>}
                  </ul>
                </div>
              ) : (
                <p className="success">Validation will run once more before the atomic commit.</p>
              )}
              <div className="checkin-actions">
                <button type="button" className="button-primary"
                  disabled={editBusy || editAssemblyReviewBlocked}
                  onClick={() => void submitEdit()}>
                  {editBusy ? "Committing…" : "Commit all changes"}
                </button>
                <button type="button" disabled={editBusy} onClick={() => setShowEditReview(false)}>
                  Back
                </button>
              </div>
            </div>
          )}
          {committedRevisionId && (
            <div className="commit-processing-status" aria-live="polite">
              <div className="commit-processing-heading">
                <div>
                  <h3>Post-commit processing</h3>
                  {!commitProcessingLoaded && <span className="status-badge checking">Loading…</span>}
                  {commitProcessingJobs.some((job) => ["PENDING", "RUNNING"].includes(job.state))
                    && <span className="status-badge uploading">Processing</span>}
                  {commitProcessingJobs.length > 0
                    && commitProcessingJobs.every((job) => job.state === "SUCCEEDED")
                    && <span className="status-badge complete">Done</span>}
                  {commitProcessingJobs.some((job) => job.state === "FAILED")
                    && <span className="status-badge error">Action required</span>}
                </div>
                {committedItemId && (
                  <Link to={`/items/${committedItemId}/revisions/${committedRevisionId}`}>
                    Open committed revision
                  </Link>
                )}
              </div>
              {commitProcessingError && <p className="commit-processing-error">{commitProcessingError}</p>}
              {commitProcessingJobs.map((job) => (
                <article className="commit-processing-job" key={job.id}>
                  <div><strong>{job.type.replaceAll("_", " ")}</strong><span>{job.progress_percent}%</span></div>
                  <progress max="100" value={job.progress_percent} />
                  <p>{job.state === "FAILED" ? job.error_detail ?? "Processing failed." : job.stage.replaceAll("_", " ")}</p>
                  {job.state === "FAILED" && (
                    <button type="button" onClick={() => void retryCommitProcessing(job.id)}>Retry processing</button>
                  )}
                </article>
              ))}
              <p className="muted">Retry starts a new processing job; it does not repeat the committed revision changes.</p>
            </div>
          )}
          <p className="muted">Only staged CAD and attachment changes are submitted. Preview files are regenerated after commit.</p>
        </section>
      )}
    </section>
  );

}
