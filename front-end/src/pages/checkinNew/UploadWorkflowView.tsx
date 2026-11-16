import { Link } from 'react-router-dom';
import { createsManagedItem, isDrawing, logicalPathDepth } from './model';
import type { CheckinController } from './useCheckinWorkflowController';

export function UploadWorkflowView({ controller }: { controller: CheckinController }) {
  const {
    targetItemId,
    targetItemNumber,
    targetItemName,
    targetItemType,
    targetRevision,
    resumeStageId,
    files,
    setFiles,
    numberTemplates,
    selectedNumberTemplateId,
    setSelectedNumberTemplateId,
    identityBusy,
    uploadMode,
    mainFileName,
    setMainFileName,
    selectedAssemblyPath,
    setSelectedAssemblyPath,
    fileSelectionError,
    setFileSelectionError,
    workerWarning,
    dependencyReport,
    cadSelectionBusy,
    cadSelectionStatus,
    stagingId,
    resumeStage,
    resumeStageLoading,
    validation,
    validationHistory,
    analysis,
    validationBusy,
    validationError,
    actionProposal,
    actionExecution,
    actionBusy,
    commitMessage,
    committedItemId,
    committedRevisionId,
    resumeDisplayName,
    resumeWithFiles,
    openCadFileWithLocalAgent,
    runValidation,
    proposeValidation,
    decideProposal,
    explainValidation,
    acknowledgeWarning,
    removeFile,
    uploadFile,
    uploadAll,
    readyCount,
    duplicateCount,
    completeCount,
    assemblyMainCandidates,
    renderEntryStatus,
    renderProperties,
  } = controller;
  return (
    <section className="page checkin-new-page">
      <div className="checkin-header">
        <div>
          <h1>{resumeStageId ? 'Resume Upload' : targetItemId ? 'Check in Item' : 'New Item'}</h1>
          <p className="page-subtitle">
            {resumeStageId
              ? 'Continue the saved stage by selecting its original file or folder.'
              : targetItemId
                ? 'Upload a new version for the checked-out Item Revision.'
                : 'Prepare files and identity choices, then create the Item in one commit.'}
          </p>
        </div>
      </div>

      {resumeStageId && (
        <div className="target-revision-banner" role="status">
          <div>
            <strong>
              {resumeStageLoading
                ? 'Loading saved stage…'
                : resumeDisplayName
                  ? `Resume saved upload — ${resumeDisplayName}`
                  : 'Resume saved upload'}
            </strong>
            <span>
              {resumeStage
                ? `${resumeStage.files.filter((file) => file.state === 'VERIFIED').length}/${resumeStage.files.length} files already verified. Select the original ${resumeStage.files.length > 1 ? 'folder' : 'file'} to continue the missing transfers.`
                : 'Select the same CAD file or folder. Already verified files will not be uploaded again.'}
            </span>
          </div>
          <div className="resume-upload-actions">
            <label className="button button-primary">
              {resumeStage?.files.length === 1 ? 'Select original file' : 'Select original folder'}
              <input
                type="file"
                hidden
                multiple={Boolean(resumeStage && resumeStage.files.length > 1)}
                ref={(input) => {
                  if (input)
                    input.webkitdirectory = Boolean(resumeStage && resumeStage.files.length > 1);
                }}
                disabled={resumeStageLoading || !resumeStage}
                onChange={(event) => {
                  const chosen = Array.from(event.target.files ?? []);
                  if (chosen.length)
                    void resumeWithFiles(chosen).catch((reason) =>
                      setFileSelectionError(
                        reason instanceof Error ? reason.message : 'Unable to resume this upload.'
                      )
                    );
                  event.target.value = '';
                }}
              />
            </label>
            <Link to={`/stages?stageId=${encodeURIComponent(resumeStageId)}`}>Back to stage</Link>
          </div>
        </div>
      )}

      {targetItemId && (
        <div className="target-revision-banner">
          <div>
            <strong>Check in {targetItemNumber}</strong>
            <span>
              {targetItemName} · Revision {targetRevision} · {targetItemType}
            </span>
          </div>
          <Link to={`/items/${targetItemId}`}>Back to item</Link>
        </div>
      )}

      {commitMessage && (
        <p className="success" role="status">
          {commitMessage}{' '}
          {committedItemId && committedRevisionId ? (
            <Link to={`/items/${committedItemId}/revisions/${committedRevisionId}`}>
              Open assembly processing
            </Link>
          ) : (
            stagingId && (
              <Link to={`/stages?stageId=${encodeURIComponent(stagingId)}`}>Monitor stage</Link>
            )
          )}
        </p>
      )}

      {!targetItemId && numberTemplates.length > 0 && (
        <label className="checkin-number-template">
          Item-number template
          <select
            value={selectedNumberTemplateId}
            onChange={(event) => setSelectedNumberTemplateId(event.target.value)}
            disabled={files.length > 0}
          >
            {numberTemplates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
                {template.is_selected ? ' (default)' : ''}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="checkin-file-select">
        <button
          type="button"
          className="file-select-button"
          disabled={
            cadSelectionBusy || identityBusy || files.some((entry) => entry.status === 'uploading')
          }
          onClick={() => void openCadFileWithLocalAgent()}
        >
          {cadSelectionBusy ? 'Analyzing CAD file…' : 'Open CAD File'}
        </button>
        {cadSelectionBusy && cadSelectionStatus && (
          <p className="file-select-hint" role="status" aria-live="polite">
            {cadSelectionStatus}
          </p>
        )}
        {files.length === 0 && (
          <p className="file-select-hint">
            Select one root CAD document. The installed CAD system finds its referenced files
            automatically.
          </p>
        )}
        {fileSelectionError && (
          <p className="error" role="alert">
            {fileSelectionError}
          </p>
        )}
        {workerWarning && (
          <p className="maintenance-warning" role="status">
            {workerWarning}
          </p>
        )}
        {dependencyReport?.validation_issues?.length ? (
          <div className="dependency-report" role="alert">
            <strong>FreeCAD dependency problems</strong>
            <p>Resolve these document-reference problems before committing the assembly.</p>
            <ul>
              {dependencyReport.validation_issues.map((issue, index) => (
                <li key={`${issue.document}-${issue.object}-${issue.property}-${index}`}>
                  {issue.object && (
                    <code>
                      {issue.object}
                      {issue.property ? `.${issue.property}` : ''}
                    </code>
                  )}
                  <span>{issue.message}</span>
                  {issue.cycle?.length ? (
                    <span>Reference cycle: {issue.cycle.join(' → ')}</span>
                  ) : null}
                  <span>
                    Document: {issue.document}
                    {issue.assembly_model ? `; assembly model: ${issue.assembly_model}` : ''}
                  </span>
                  <span>{issue.suggestion}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {dependencyReport?.missing_references.length ? (
          <div className="dependency-report" role="alert">
            <strong>Missing assembly references</strong>
            <p>
              Place these files in the locations expected by the assembly, then select the root file
              again.
            </p>
            <ul>
              {dependencyReport.missing_references.map((missing, index) => (
                <li key={`${missing.reference}-${index}`}>
                  <code>{missing.reference}</code>
                  <span>Referenced by {missing.referenced_by}</span>
                  {missing.object && (
                    <span>
                      Object: {missing.object}
                      {missing.property ? ` (${missing.property})` : ''}
                    </span>
                  )}
                  {missing.assembly_model && <span>Assembly model: {missing.assembly_model}</span>}
                  {missing.suggestion && <span>{missing.suggestion}</span>}
                </li>
              ))}
            </ul>
          </div>
        ) : dependencyReport ? (
          <p className="dependency-success" role="status">
            {dependencyReport.document_type === 'ASSEMBLY'
              ? `Assembly references resolved: ${dependencyReport.files.length} required files packaged. `
              : `${dependencyReport.document_type === 'DRAWING' ? 'Drawing' : 'Part'} document packaged. `}
            The package was reopened successfully in isolation.
          </p>
        ) : null}
        {dependencyReport?.missing_supporting_references?.length ? (
          <div className="validation-warning" role="status">
            <strong>Some optional linked resources could not be resolved</strong>
            <p>The CAD assembly is complete and can still be uploaded.</p>
            <ul>
              {dependencyReport.missing_supporting_references.map((missing, index) => (
                <li key={`${missing.reference}-${index}`}>
                  <code>{missing.reference || 'Unidentified linked resource'}</code>
                  <span>Referenced by {missing.referenced_by}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      {files.length > 0 && (
        <>
          <div className="checkin-summary">
            <div className="summary-stat">
              <span className="stat-label">Total Files</span>
              <strong className="stat-value">{files.length}</strong>
            </div>
            <div className="summary-stat">
              <span className="stat-label">Duplicates</span>
              <strong className="stat-value duplicate">{duplicateCount}</strong>
            </div>
            <div className="summary-stat">
              <span className="stat-label">Ready to Upload</span>
              <strong className="stat-value">{readyCount}</strong>
            </div>
            <div className="summary-stat">
              <span className="stat-label">Completed</span>
              <strong className="stat-value success">{completeCount}</strong>
            </div>
          </div>

          <div className="checkin-actions">
            {stagingId && (
              <>
                <button
                  type="button"
                  onClick={() => void runValidation()}
                  disabled={validationBusy || actionBusy}
                  className="button-secondary"
                >
                  {validationBusy
                    ? 'Validating…'
                    : validation
                      ? 'Revalidate staging'
                      : 'Validate staging'}
                </button>
                <button
                  type="button"
                  onClick={() => void proposeValidation()}
                  disabled={validationBusy || actionBusy || actionProposal?.status === 'PENDING'}
                  className="button-secondary"
                >
                  {actionBusy ? 'Preparing action…' : 'Propose AI validation'}
                </button>
              </>
            )}
          </div>

          {actionProposal && (
            <section
              className={`ai-action-card ${actionProposal.risk.toLowerCase()}`}
              aria-live="polite"
            >
              <div className="ai-action-title-row">
                <div>
                  <span className="ai-action-label">AI proposed action</span>
                  <h2>{actionProposal.preview.title ?? actionProposal.tool_name}</h2>
                </div>
                <span className="ai-action-risk">{actionProposal.risk} risk</span>
              </div>
              <p>{actionProposal.preview.summary}</p>
              <dl>
                <div>
                  <dt>Target</dt>
                  <dd>{actionProposal.preview.target ?? actionProposal.target_id}</dd>
                </div>
                <div>
                  <dt>Persistent change</dt>
                  <dd>{actionProposal.preview.persistent_change}</dd>
                </div>
                <div>
                  <dt>Why confirm?</dt>
                  <dd>{actionProposal.preview.confirmation_reason}</dd>
                </div>
                <div>
                  <dt>Expires</dt>
                  <dd>{new Date(actionProposal.expires_at).toLocaleString()}</dd>
                </div>
              </dl>
              {actionProposal.status === 'PENDING' ? (
                <div className="ai-action-buttons">
                  <button
                    type="button"
                    className="button-primary"
                    disabled={actionBusy}
                    onClick={() => void decideProposal(true)}
                  >
                    Confirm exact action
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={actionBusy}
                    onClick={() => void decideProposal(false)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <p className="ai-action-outcome">
                  Proposal {actionProposal.status.toLowerCase()}.
                  {actionExecution?.status === 'SUCCEEDED' &&
                    ' Backend validation completed successfully.'}
                  {actionExecution?.status === 'FAILED' &&
                    ` ${actionExecution.error.message ?? 'Execution failed.'}`}
                </p>
              )}
            </section>
          )}

          {stagingId && (
            <section className="validation-panel" aria-labelledby="validation-heading">
              <div className="validation-heading-row">
                <div>
                  <h2 id="validation-heading">Staging validation</h2>
                  <p>Deterministic findings describe the exact staging snapshot used by Commit.</p>
                </div>
                {validation && (
                  <span className={`validation-status ${validation.status.toLowerCase()}`}>
                    {validation.status.replaceAll('_', ' ')}
                  </span>
                )}
              </div>

              {!validation && !validationError && (
                <p className="validation-empty">
                  Run validation to check file verification, engineering metadata, geometry, and
                  duplicate evidence available in this staging session.
                </p>
              )}
              {validationError && (
                <div className="validation-error" role="alert">
                  {validationError}
                </div>
              )}

              {validation && (
                <>
                  <div className="validation-summary" aria-label="Validation summary">
                    <span>
                      <strong>{validation.summary.blocking}</strong> blocking
                    </span>
                    <span>
                      <strong>{validation.summary.error + validation.summary.critical}</strong>{' '}
                      errors
                    </span>
                    <span>
                      <strong>{validation.summary.warning}</strong> warnings
                    </span>
                    <span>
                      <strong>{validation.summary.total}</strong> total
                    </span>
                  </div>
                  <div className="validator-states">
                    {Object.entries(validation.validator_states).map(([name, state]) => (
                      <span key={name} className={`validator-state ${state.toLowerCase()}`}>
                        {name}: {state.replaceAll('_', ' ')}
                      </span>
                    ))}
                  </div>
                  {validationHistory.length > 0 && (
                    <details className="validation-history">
                      <summary>Validation history ({validationHistory.length})</summary>
                      <div
                        className="validation-history-table"
                        role="region"
                        aria-label="Validation history"
                        tabIndex={0}
                      >
                        <table>
                          <thead>
                            <tr>
                              <th>Run</th>
                              <th>Status</th>
                              <th>Blocking</th>
                              <th>Warnings</th>
                              <th>Report identity</th>
                            </tr>
                          </thead>
                          <tbody>
                            {validationHistory.map((run) => (
                              <tr
                                key={run.validation_id}
                                aria-current={
                                  run.validation_id === validation.validation_id
                                    ? 'true'
                                    : undefined
                                }
                              >
                                <td>#{run.run_number}</td>
                                <td>{run.status.replaceAll('_', ' ')}</td>
                                <td>{run.summary.blocking}</td>
                                <td>{run.summary.warning}</td>
                                <td>
                                  <code title={run.report_hash}>
                                    {run.report_hash.slice(0, 12)}
                                  </code>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="validation-history-note">
                        Earlier reports remain immutable. Compare run counts to confirm which
                        findings were resolved after staged evidence changed.
                      </p>
                    </details>
                  )}
                  {validation.findings.length === 0 ? (
                    <p className="validation-ready-message">No active deterministic findings.</p>
                  ) : (
                    <ol className="validation-findings">
                      {validation.findings.map((finding) => (
                        <li
                          key={finding.id}
                          className={`validation-finding ${finding.severity.toLowerCase()}`}
                        >
                          <div className="validation-finding-title">
                            <strong>{finding.title}</strong>
                            <span>
                              {finding.severity}
                              {finding.blocking ? ' · BLOCKING' : ''}
                            </span>
                          </div>
                          <p>{finding.message}</p>
                          <div className="validation-finding-meta">
                            <code>{finding.code}</code>
                            <span>{finding.category}</span>
                            {finding.actions.length > 0 && (
                              <span>
                                Suggested:{' '}
                                {finding.actions
                                  .map((action) => action.replaceAll('_', ' '))
                                  .join(', ')}
                              </span>
                            )}
                          </div>
                          {finding.actions.includes('ignore_warning') &&
                            (finding.acknowledged ? (
                              <p className="validation-acknowledged">
                                ✓ Warning reviewed and acknowledged
                              </p>
                            ) : (
                              <button
                                type="button"
                                className="button-small"
                                disabled={validationBusy}
                                onClick={() => void acknowledgeWarning(finding.id)}
                              >
                                Acknowledge warning
                              </button>
                            ))}
                        </li>
                      ))}
                    </ol>
                  )}
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => void explainValidation()}
                    disabled={validationBusy}
                  >
                    Explain findings
                  </button>
                </>
              )}

              {analysis && (
                <div className="validation-analysis">
                  <div className="validation-analysis-heading">
                    <h3>{analysis.output.headline}</h3>
                    <span>
                      {analysis.mode === 'AI'
                        ? `AI · ${analysis.model ?? 'configured model'}`
                        : 'Deterministic'}
                    </span>
                  </div>
                  <p>{analysis.output.summary}</p>
                  {analysis.output.issues.map((issue) => {
                    const finding = validation?.findings.find(
                      (entry) => entry.id === issue.finding_id
                    );
                    return (
                      <div className="validation-analysis-issue" key={issue.finding_id}>
                        <strong>{finding?.title ?? 'Validation finding'}</strong>
                        <p>{issue.explanation}</p>
                        <p>{issue.impact}</p>
                        {issue.recommended_actions.length > 0 && (
                          <p>
                            Recommended:{' '}
                            {issue.recommended_actions.map((item) => item.label).join(', ')}
                          </p>
                        )}
                      </div>
                    );
                  })}
                  {analysis.warnings.map((warning) => (
                    <p className="validation-analysis-warning" key={warning}>
                      {warning}
                    </p>
                  ))}
                </div>
              )}
            </section>
          )}

          {uploadMode === 'single' ? (
            <section className="checkin-single-editor">
              {renderProperties(files[0], 0)}
              <footer className="checkin-editor-footer">
                <div>
                  {files[0].status === 'complete' && files[0].createdItemId && (
                    <Link to={`/items/${files[0].createdItemId}`}>Open Item</Link>
                  )}
                </div>
                <div className="checkin-footer-actions">
                  {files[0].status !== 'uploading' && files[0].status !== 'complete' && (
                    <button
                      type="button"
                      className="button-secondary button-danger-outline"
                      onClick={() => removeFile(0)}
                    >
                      Remove file
                    </button>
                  )}
                  <button
                    type="button"
                    className="button-primary"
                    disabled={
                      readyCount === 0 ||
                      identityBusy ||
                      files.some((entry) => entry.status === 'uploading')
                    }
                    onClick={() => void uploadFile(0)}
                  >
                    {resumeStageId
                      ? 'Resume upload'
                      : targetItemId
                        ? 'Upload'
                        : files[0].isDuplicate
                          ? 'Use existing Item'
                          : 'Commit New Item'}
                  </button>
                </div>
              </footer>
            </section>
          ) : (
            <section className="assembly-editor">
              <div className="assembly-file-panel">
                <div className="assembly-panel-heading">
                  <div>
                    <span className="checkin-eyebrow">Assembly contents</span>
                    <h2>Parts</h2>
                  </div>
                  <span>{files.length} files</span>
                </div>
                <label className="assembly-main-select">
                  Main assembly file
                  <select
                    value={mainFileName}
                    disabled={identityBusy || files.some((entry) => entry.status === 'uploading')}
                    onChange={(event) => {
                      setMainFileName(event.target.value);
                      setSelectedAssemblyPath(event.target.value);
                      setFiles((previous) =>
                        previous.map((entry) => ({ ...entry, formError: undefined }))
                      );
                    }}
                  >
                    {assemblyMainCandidates.map((entry) => (
                      <option key={entry.logicalPath} value={entry.logicalPath}>
                        {logicalPathDepth(entry.logicalPath) === 0
                          ? 'Root · '
                          : `${logicalPathDepth(entry.logicalPath)} folder${logicalPathDepth(entry.logicalPath) === 1 ? '' : 's'} deep · `}
                        {entry.logicalPath}
                      </option>
                    ))}
                  </select>
                  <span>Choose the root part or assembly for this upload.</span>
                </label>
                <div className="assembly-file-list" role="listbox" aria-label="Assembly files">
                  <div className="assembly-file-list-header" aria-hidden="true">
                    <span>File / Item</span>
                    <span>Action</span>
                  </div>
                  {files.map((entry, index) => (
                    <div
                      key={entry.logicalPath}
                      role="option"
                      aria-selected={entry.logicalPath === selectedAssemblyPath}
                      className={`assembly-file-row ${entry.logicalPath === selectedAssemblyPath ? 'selected' : ''} ${entry.formError ? 'has-error' : ''}`}
                      onClick={() => setSelectedAssemblyPath(entry.logicalPath)}
                    >
                      <button
                        type="button"
                        className="assembly-file-identity"
                        onClick={() => setSelectedAssemblyPath(entry.logicalPath)}
                      >
                        <strong>{entry.file.name}</strong>
                        <span>
                          {createsManagedItem(entry, dependencyReport)
                            ? entry.itemNumber || 'Item number pending'
                            : isDrawing(entry)
                              ? 'Drawing · attached to model'
                              : 'Supporting CAD file'}
                        </span>
                        {renderEntryStatus(entry)}
                      </button>
                      <button
                        type="button"
                        className="button-small button-danger"
                        disabled={entry.status === 'uploading' || entry.status === 'complete'}
                        onClick={(event) => {
                          event.stopPropagation();
                          removeFile(index);
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>
              <div className="assembly-properties-panel">
                {files.findIndex((entry) => entry.logicalPath === selectedAssemblyPath) >= 0 ? (
                  renderProperties(
                    files[files.findIndex((entry) => entry.logicalPath === selectedAssemblyPath)],
                    files.findIndex((entry) => entry.logicalPath === selectedAssemblyPath)
                  )
                ) : (
                  <p className="muted">Select a part to edit its properties.</p>
                )}
              </div>
              <footer className="checkin-editor-footer assembly-editor-footer">
                <p className="muted">
                  Select a row to edit all properties. Only one file can be the main assembly.
                </p>
                <div className="checkin-footer-actions">
                  <button
                    type="button"
                    className="button-primary"
                    onClick={() => void uploadAll()}
                    disabled={
                      readyCount === 0 ||
                      identityBusy ||
                      files.some((entry) => entry.status === 'uploading')
                    }
                  >
                    {resumeStageId
                      ? `Resume upload (${readyCount})`
                      : `Commit Assembly (${readyCount})`}
                  </button>
                </div>
              </footer>
            </section>
          )}
        </>
      )}
    </section>
  );
}
