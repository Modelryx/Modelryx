import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import './Stages.css';

type StageSummary = {
  id: string;
  status: string;
  operation: string;
  snapshot_version: number;
  owner_id: string;
  owner_name: string;
  file_count: number;
  verified_file_count: number;
  item_id: string | null;
  item_number: string | null;
  revision_id: string | null;
  created_revision_ids: string[];
  created_at: string;
  updated_at: string;
  can_cancel: boolean;
  can_resume: boolean;
  workflow_state: string | null;
  workflow_stage: string | null;
  workflow_progress_percent: number | null;
  workflow_error_detail: string | null;
};
type StageDetail = StageSummary & {
  proposal: Record<string, unknown>;
  result: Record<string, unknown>;
  files: Array<{
    id: string;
    logical_path: string;
    filename: string;
    byte_size: number;
    state: string;
  }>;
};
type StageList = { items: StageSummary[]; total: number };
type StageWorkflow = {
  state: string;
  stage: string;
  progress_percent: number;
  error_detail: string | null;
};
type ProcessingFile = {
  id: string;
  logical_path: string;
  filename: string;
  byte_size: number;
  role: string | null;
  status: 'QUEUE' | 'PROCESSING' | 'ERROR' | 'WARNING' | 'FINISH';
  stage: string | null;
  message: string | null;
  fix_suggestion: string | null;
};
export type StageProcessingJob = {
  id: string;
  revision_id: string;
  type: string;
  state: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  stage: string;
  progress_percent: number;
  error_detail: string | null;
  attempt_count: number;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
  can_control: boolean;
  files: ProcessingFile[];
  fix_suggestion: string | null;
  item_number?: string | null;
  item_name?: string | null;
};

const when = (value: string) => new Date(value).toLocaleString();
const size = (value: number) =>
  value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KB`
    : `${(value / 1024 / 1024).toFixed(1)} MB`;

const processingStatus = (job: StageProcessingJob | undefined) => {
  if (!job || job.state === 'PENDING') return 'QUEUE';
  if (job.state === 'RUNNING') return 'PROCESSING';
  if (job.state === 'FAILED') return 'FAIL';
  return 'FINISH';
};

function storedAdministratorRole() {
  try {
    const roles = JSON.parse(localStorage.getItem('modelryx-roles') ?? '[]');
    return (
      (Array.isArray(roles) && roles.includes('administrator')) ||
      localStorage.getItem('modelryx-role') === 'administrator'
    );
  } catch {
    return localStorage.getItem('modelryx-role') === 'administrator';
  }
}

export function StagesPanel({
  revisionId,
  isAdmin: adminOverride,
  processingJobs = [],
  onProcessingChanged,
  initialStageId = null,
}: {
  revisionId?: string;
  isAdmin?: boolean;
  processingJobs?: StageProcessingJob[];
  onProcessingChanged?: () => Promise<void>;
  initialStageId?: string | null;
}) {
  const isAdmin = adminOverride ?? storedAdministratorRole();
  const [scope, setScope] = useState<'ongoing' | 'finished' | 'all'>(
    revisionId ? 'all' : 'ongoing'
  );
  const [ownerId, setOwnerId] = useState('');
  const [owners, setOwners] = useState<Array<{ id: string; display_name: string }>>([]);
  const [stages, setStages] = useState<StageSummary[]>([]);
  const [listedProcessingJobs, setListedProcessingJobs] = useState<StageProcessingJob[]>([]);
  const [selected, setSelected] = useState<StageDetail | null>(null);
  const [selectedJobId, setSelectedJobId] = useState(() =>
    revisionId ? (processingJobs[0]?.id ?? '') : ''
  );
  const [busyJobId, setBusyJobId] = useState('');
  const [busyStageId, setBusyStageId] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [detailRefreshing, setDetailRefreshing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams({ state: revisionId ? scope : 'all', limit: '100' });
      if (revisionId) query.set('revision_id', revisionId);
      if (isAdmin && ownerId) query.set('owner_id', ownerId);
      const [result, listedJobs] = await Promise.all([
        api<StageList>(`/staging?${query}`),
        revisionId
          ? Promise.resolve(null)
          : api<StageProcessingJob[]>(
              `/processing-jobs?${new URLSearchParams({
                state: 'all',
                limit: '100',
                ...(isAdmin && ownerId ? { owner_id: ownerId } : {}),
              })}`
            ),
      ]);
      setStages(result.items);
      if (listedJobs) setListedProcessingJobs(listedJobs);
      setSelected((current) => {
        if (!current) return current;
        const summary = result.items.find((row) => row.id === current.id);
        return summary ? { ...current, ...summary } : null;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load stages');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, ownerId, revisionId, scope]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (isAdmin && !revisionId) {
      void api<Array<{ id: string; display_name: string }>>('/admin/users')
        .then(setOwners)
        .catch(() => undefined);
    }
  }, [isAdmin, revisionId]);

  const selectStage = async (stage: StageSummary) => {
    setError('');
    setSelectedJobId('');
    try {
      const detail = await api<Pick<StageDetail, 'proposal' | 'result' | 'files'>>(
        `/staging/${stage.id}`
      );
      setSelected({ ...stage, ...detail });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load stage');
    }
  };
  const refreshSelectedDetail = useCallback(async () => {
    const selectedId = selected?.id;
    if (!selectedId) return;
    setDetailRefreshing(true);
    try {
      const [detail, workflow] = await Promise.all([
        api<Pick<StageDetail, 'proposal' | 'result' | 'files' | 'status' | 'snapshot_version'>>(
          `/staging/${selectedId}`
        ),
        api<StageWorkflow>(`/staging/${selectedId}/workflow`),
      ]);
      setSelected((current) => {
        if (current?.id !== selectedId) return current;
        const terminal = ['COMPLETED', 'CANCELLED', 'PROMOTED'].includes(detail.status);
        return {
          ...current,
          ...detail,
          workflow_state: terminal || workflow.state === 'NOT_SUBMITTED' ? null : workflow.state,
          workflow_stage: terminal ? null : workflow.stage,
          workflow_progress_percent: terminal ? 100 : workflow.progress_percent,
          workflow_error_detail: workflow.error_detail,
        };
      });
    } catch {
      // Keep the last usable detail visible during a transient polling failure.
    } finally {
      setDetailRefreshing(false);
    }
  }, [selected?.id]);
  const refresh = async () => {
    await load();
    if (selected?.id) await refreshSelectedDetail();
    if (selectedJobId) await onProcessingChanged?.();
  };
  useEffect(() => {
    if (!initialStageId || selected?.id === initialStageId) return;
    const stage = stages.find((row) => row.id === initialStageId);
    if (stage) void selectStage(stage);
  }, [initialStageId, stages, selected?.id]);
  const selectProcessing = (job: StageProcessingJob) => {
    setSelectedJobId(job.id);
    setError('');
  };
  const runProcessingAction = async (job: StageProcessingJob, action: 'retry' | 'cancel') => {
    if (action === 'cancel' && !confirm('Cancel this queued processing stage?')) return;
    setBusyJobId(job.id);
    setError('');
    try {
      await api(`/processing-jobs/${job.id}/${action}`, 'POST');
      await onProcessingChanged?.();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to ${action} processing stage`);
    } finally {
      setBusyJobId('');
    }
  };
  const cancel = async () => {
    if (!selected || !confirm('Cancel this ongoing stage?')) return;
    try {
      await api(`/staging/${selected.id}`, 'DELETE');
      setSelected(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to cancel stage');
    }
  };
  const runStageAction = async (action: 'retry' | 'pause') => {
    if (!selected) return;
    setBusyStageId(selected.id);
    setError('');
    try {
      await api(`/staging/${selected.id}/${action}`, 'POST');
      await load();
      await selectStage(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to ${action} stage`);
    } finally {
      setBusyStageId('');
    }
  };
  const availableProcessingJobs = revisionId ? processingJobs : listedProcessingJobs;
  const jobsForStage = (stage: StageSummary) => {
    const revisionIds = new Set(
      [stage.revision_id, ...(stage.created_revision_ids ?? [])].filter(Boolean)
    );
    const latestByRevision = new Map<string, StageProcessingJob>();
    availableProcessingJobs.forEach((job) => {
      if (revisionIds.has(job.revision_id) && !latestByRevision.has(job.revision_id)) {
        latestByRevision.set(job.revision_id, job);
      }
    });
    return [...latestByRevision.values()];
  };
  const visibleStages = stages.filter((stage) => {
    if (scope === 'all' || revisionId) return true;
    const stageJobs = jobsForStage(stage);
    const processing = stageJobs.some((job) => ['PENDING', 'RUNNING'].includes(job.state));
    const staging = !['COMPLETED', 'CANCELLED', 'PROMOTED'].includes(stage.status);
    return scope === 'ongoing' ? staging || processing : !staging && !processing;
  });
  const selectedJob = availableProcessingJobs.find((job) => job.id === selectedJobId) ?? null;
  const selectedStageJobs = selected ? jobsForStage(selected) : [];

  return (
    <section className="stages-panel">
      <header className="stages-heading">
        <div>
          <h2>{revisionId ? 'Revision Stages' : isAdmin ? 'All Stages' : 'My Stages'}</h2>
          <p>Review upload, commit, conversion, metadata, and preview work in one place.</p>
        </div>
        <div className="stage-filters">
          <button
            type="button"
            disabled={loading || detailRefreshing}
            onClick={() => void refresh()}
          >
            {loading || detailRefreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          {isAdmin && !revisionId && (
            <label>
              User{' '}
              <select value={ownerId} onChange={(event) => setOwnerId(event.target.value)}>
                <option value="">All users</option>
                {owners.map((owner) => (
                  <option key={owner.id} value={owner.id}>
                    {owner.display_name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Status{' '}
            <select
              value={scope}
              onChange={(event) => setScope(event.target.value as typeof scope)}
            >
              <option value="ongoing">Ongoing</option>
              <option value="finished">Finished</option>
              <option value="all">All</option>
            </select>
          </label>
        </div>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="stages-split">
        <aside className="stages-list" aria-label="Stages">
          {loading && <p className="muted">Loading…</p>}
          {!loading && !visibleStages.length && <p className="muted">No matching stages.</p>}
          {visibleStages.length > 0 && <h3>Upload and commit</h3>}
          {visibleStages.map((stage) => (
            <div className="stage-list-row" key={stage.id}>
              <button
                type="button"
                className={selected?.id === stage.id ? 'active' : ''}
                onClick={() => void selectStage(stage)}
              >
                <span>
                  <strong>{stage.operation.replaceAll('_', ' ')}</strong>
                  <em>{(stage.workflow_state || stage.status).replaceAll('_', ' ')}</em>
                </span>
                <small>
                  {stage.item_number ?? `${stage.file_count} file(s)`} · {stage.owner_name}
                </small>
                <time>{when(stage.updated_at)}</time>
              </button>
              {stage.can_resume &&
                stage.verified_file_count < stage.file_count &&
                !['QUEUED', 'RUNNING', 'PAUSED'].includes(stage.workflow_state ?? '') && (
                  <Link
                    className="stage-list-resume"
                    to={`/checkin?resumeStageId=${encodeURIComponent(stage.id)}`}
                  >
                    Resume upload
                  </Link>
                )}
            </div>
          ))}
        </aside>
        <article className="stage-detail">
          {selectedJob && !selected ? (
            <>
              <div className="stage-detail-title">
                <div>
                  <h3>{selectedJob.item_number ?? selectedJob.type.replaceAll('_', ' ')}</h3>
                  {selectedJob.item_name && <p>{selectedJob.item_name}</p>}
                  <p>
                    {selectedJob.stage.replaceAll('_', ' ')} · {selectedJob.progress_percent}%
                  </p>
                </div>
                {selectedJob.can_control && (
                  <div>
                    {selectedJob.state === 'FAILED' && selectedJob.stage !== 'CANCELLED' && (
                      <button
                        type="button"
                        disabled={busyJobId === selectedJob.id}
                        onClick={() => void runProcessingAction(selectedJob, 'retry')}
                      >
                        Retry
                      </button>
                    )}
                    {(selectedJob.state === 'PENDING' ||
                      (selectedJob.state === 'RUNNING' &&
                        selectedJob.type === 'FCSTD_TO_MODELRYX' &&
                        !['PUBLISHING', 'COMPLETED'].includes(selectedJob.stage))) && (
                      <button
                        type="button"
                        disabled={
                          busyJobId === selectedJob.id || selectedJob.stage === 'CANCELLING'
                        }
                        onClick={() => void runProcessingAction(selectedJob, 'cancel')}
                      >
                        {selectedJob.stage === 'CANCELLING' ? 'Cancelling…' : 'Cancel'}
                      </button>
                    )}
                  </div>
                )}
              </div>
              <progress
                max="100"
                value={selectedJob.progress_percent}
                aria-label="Processing progress"
              />
              <dl className="stage-summary">
                <div>
                  <dt>Status</dt>
                  <dd>{selectedJob.state.replaceAll('_', ' ')}</dd>
                </div>
                <div>
                  <dt>Attempt</dt>
                  <dd>{selectedJob.attempt_count}</dd>
                </div>
                <div>
                  <dt>Queued</dt>
                  <dd>{when(selectedJob.available_at)}</dd>
                </div>
                <div>
                  <dt>Files</dt>
                  <dd>{selectedJob.files.length}</dd>
                </div>
              </dl>
              {selectedJob.error_detail && (
                <div className="stage-diagnostic error">
                  <strong>Error</strong>
                  <p>{selectedJob.error_detail}</p>
                </div>
              )}
              {selectedJob.fix_suggestion && (
                <div className="stage-diagnostic suggestion">
                  <strong>Suggested fix</strong>
                  <p>{selectedJob.fix_suggestion}</p>
                </div>
              )}
              <h4>Files</h4>
              {selectedJob.files.length ? (
                <div className="stage-files processing-files">
                  {selectedJob.files.map((file) => (
                    <div key={file.id}>
                      <span>
                        <strong>{file.logical_path}</strong>
                        <small>
                          {file.role ?? 'REFERENCE'} · {size(file.byte_size)}
                        </small>
                        {file.stage && <small>{file.stage.replaceAll('_', ' ')}</small>}
                        {file.message && <small className="file-message">{file.message}</small>}
                        {file.fix_suggestion && (
                          <small className="file-suggestion">Fix: {file.fix_suggestion}</small>
                        )}
                      </span>
                      <span className={`file-stage-status status-${file.status.toLowerCase()}`}>
                        {file.status}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">No source files are attached to this processing stage.</p>
              )}
            </>
          ) : !selected ? (
            <p className="muted">Select a stage to inspect its content.</p>
          ) : (
            <>
              <div className="stage-detail-title">
                <div>
                  <h3>{selected.operation.replaceAll('_', ' ')}</h3>
                  <p>
                    {(
                      selected.workflow_stage ||
                      selected.workflow_state ||
                      selected.status
                    ).replaceAll('_', ' ')}{' '}
                    · {selected.owner_name}
                    {detailRefreshing && (
                      <span className="stage-detail-refreshing"> · Updating…</span>
                    )}
                  </p>
                </div>
                <div>
                  {selected.can_cancel && (
                    <button type="button" onClick={() => void cancel()}>
                      Cancel stage
                    </button>
                  )}
                  {selected.workflow_state === 'QUEUED' && (
                    <button
                      type="button"
                      disabled={busyStageId === selected.id}
                      onClick={() => void runStageAction('pause')}
                    >
                      {busyStageId === selected.id ? 'Pausing…' : 'Pause'}
                    </button>
                  )}
                  {['FAILED', 'PAUSED'].includes(selected.workflow_state ?? '') && (
                    <button
                      type="button"
                      disabled={busyStageId === selected.id}
                      onClick={() => void runStageAction('retry')}
                    >
                      {busyStageId === selected.id
                        ? 'Starting…'
                        : selected.workflow_state === 'PAUSED'
                          ? 'Resume'
                          : 'Retry stage'}
                    </button>
                  )}
                  {selected.can_resume &&
                    selected.files.some((file) => file.state !== 'VERIFIED') &&
                    !['QUEUED', 'RUNNING', 'PAUSED'].includes(selected.workflow_state ?? '') && (
                      <Link
                        className="secondary-button"
                        to={`/checkin?resumeStageId=${encodeURIComponent(selected.id)}`}
                      >
                        Resume upload
                      </Link>
                    )}
                  {selected.item_id && selected.revision_id && (
                    <Link
                      className="secondary-button"
                      to={`/items/${selected.item_id}/revisions/${selected.revision_id}`}
                    >
                      Open Revision
                    </Link>
                  )}
                </div>
              </div>
              {selected.workflow_progress_percent !== null && (
                <progress
                  max="100"
                  value={selected.workflow_progress_percent}
                  aria-label="Stage progress"
                />
              )}
              {selected.workflow_error_detail && (
                <div className="stage-diagnostic error">
                  <strong>Workflow issue</strong>
                  <p>{selected.workflow_error_detail}</p>
                </div>
              )}
              <h4>Assembly and parts</h4>
              {selectedStageJobs.length ? (
                <div
                  className="stage-components"
                  role="table"
                  aria-label="Assembly and part processing"
                >
                  <div className="stage-components-header" role="row">
                    <span role="columnheader">Part Number</span>
                    <span role="columnheader">File Name</span>
                    <span role="columnheader">Status</span>
                  </div>
                  {selectedStageJobs.map((job) => {
                    const primary =
                      job.files.find((file) => file.role === 'PRIMARY') ?? job.files[0];
                    const status = processingStatus(job);
                    return (
                      <button
                        type="button"
                        role="row"
                        key={job.id}
                        className={selectedJobId === job.id ? 'active' : ''}
                        onClick={() => selectProcessing(job)}
                      >
                        <span role="cell">
                          <strong>{job.item_number ?? '—'}</strong>
                        </span>
                        <span role="cell">{primary?.filename ?? '—'}</span>
                        <span
                          role="cell"
                          className={`file-stage-status status-${status.toLowerCase()}`}
                        >
                          {status}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p className="muted">Processing has not been queued for this item yet.</p>
              )}
              {selectedJob && (
                <section className="stage-component-detail">
                  <div className="stage-detail-title">
                    <div>
                      <h4>{selectedJob.item_number ?? selectedJob.type.replaceAll('_', ' ')}</h4>
                      {selectedJob.item_name && <p>{selectedJob.item_name}</p>}
                      <p>
                        {selectedJob.stage.replaceAll('_', ' ')} · {selectedJob.progress_percent}%
                      </p>
                    </div>
                    {selectedJob.can_control && (
                      <div>
                        {selectedJob.state === 'FAILED' && selectedJob.stage !== 'CANCELLED' && (
                          <button
                            type="button"
                            disabled={busyJobId === selectedJob.id}
                            onClick={() => void runProcessingAction(selectedJob, 'retry')}
                          >
                            Retry
                          </button>
                        )}
                        {(selectedJob.state === 'PENDING' ||
                          (selectedJob.state === 'RUNNING' &&
                            selectedJob.type === 'FCSTD_TO_MODELRYX' &&
                            !['PUBLISHING', 'COMPLETED'].includes(selectedJob.stage))) && (
                          <button
                            type="button"
                            disabled={
                              busyJobId === selectedJob.id || selectedJob.stage === 'CANCELLING'
                            }
                            onClick={() => void runProcessingAction(selectedJob, 'cancel')}
                          >
                            {selectedJob.stage === 'CANCELLING' ? 'Cancelling…' : 'Cancel'}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  <progress
                    max="100"
                    value={selectedJob.progress_percent}
                    aria-label="Component processing progress"
                  />
                  {selectedJob.error_detail && (
                    <div className="stage-diagnostic error">
                      <strong>Error</strong>
                      <p>{selectedJob.error_detail}</p>
                    </div>
                  )}
                  {selectedJob.fix_suggestion && (
                    <div className="stage-diagnostic suggestion">
                      <strong>Suggested fix</strong>
                      <p>{selectedJob.fix_suggestion}</p>
                    </div>
                  )}
                  <h4>Files</h4>
                  <div className="stage-files processing-files">
                    {selectedJob.files.map((file) => (
                      <div key={file.id}>
                        <span>
                          <strong>{file.logical_path}</strong>
                          <small>
                            {file.role ?? 'REFERENCE'} · {size(file.byte_size)}
                          </small>
                          {file.stage && <small>{file.stage.replaceAll('_', ' ')}</small>}
                          {file.message && <small className="file-message">{file.message}</small>}
                          {file.fix_suggestion && (
                            <small className="file-suggestion">Fix: {file.fix_suggestion}</small>
                          )}
                        </span>
                        <span className={`file-stage-status status-${file.status.toLowerCase()}`}>
                          {file.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              <dl className="stage-summary">
                <div>
                  <dt>Created</dt>
                  <dd>{when(selected.created_at)}</dd>
                </div>
                <div>
                  <dt>Updated</dt>
                  <dd>{when(selected.updated_at)}</dd>
                </div>
                <div>
                  <dt>Snapshot</dt>
                  <dd>{selected.snapshot_version}</dd>
                </div>
                <div>
                  <dt>Files</dt>
                  <dd>{selected.files.length}</dd>
                </div>
              </dl>
              <h4>Files</h4>
              <div className="stage-files">
                {selected.files.map((file) => (
                  <div key={file.id}>
                    <span>
                      <strong>{file.logical_path}</strong>
                      <small>{file.state}</small>
                    </span>
                    <span>{size(file.byte_size)}</span>
                  </div>
                ))}
              </div>
              <details>
                <summary>Proposal</summary>
                <pre>{JSON.stringify(selected.proposal, null, 2)}</pre>
              </details>
              <details>
                <summary>Result</summary>
                <pre>{JSON.stringify(selected.result, null, 2)}</pre>
              </details>
            </>
          )}
        </article>
      </div>
    </section>
  );
}

export function StagesPage({ isAdmin }: { isAdmin: boolean }) {
  const [searchParams] = useSearchParams();
  return (
    <div className="stages-page">
      <StagesPanel isAdmin={isAdmin} initialStageId={searchParams.get('stageId')} />
    </div>
  );
}
