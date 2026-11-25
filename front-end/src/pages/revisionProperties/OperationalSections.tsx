import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../workflow-api';
import { dateTime, formatPropertyName } from './formatters';
import type { ContentSnapshot, ProcessingJob } from './types';

export function ProcessingSection({
  jobs,
  onChanged,
}: {
  jobs: ProcessingJob[];
  onChanged: () => Promise<void>;
}) {
  const [busyJobId, setBusyJobId] = useState('');
  const [message, setMessage] = useState('');

  const runAction = async (job: ProcessingJob, action: 'retry' | 'cancel') => {
    if (action === 'cancel' && !window.confirm('Cancel this processing job?')) return;
    setBusyJobId(job.id);
    setMessage('');
    try {
      await api(`/processing-jobs/${job.id}/${action}`, 'POST');
      setMessage(action === 'retry' ? 'Retry queued.' : 'Pending job cancelled.');
      await onChanged();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `Unable to ${action} processing`);
    } finally {
      setBusyJobId('');
    }
  };

  return (
    <div className="item-revision-section">
      <div className="item-revision-section-header">
        <div>
          <h2>Processing</h2>
          <p className="muted">
            Persistent conversion, metadata, and preview job history for this revision.
          </p>
        </div>
      </div>
      {message && (
        <p role="status" className="muted">
          {message}
        </p>
      )}
      <section className="item-revision-panel processing-jobs-panel">
        {jobs.length ? (
          <div className="processing-job-list">
            {jobs.map((job) => (
              <article className="processing-job-card" key={job.id}>
                <div className="processing-job-heading">
                  <div>
                    <strong>{formatPropertyName(job.type)}</strong>
                    <span className={`status-badge processing-${job.state.toLowerCase()}`}>
                      {formatPropertyName(job.state)}
                    </span>
                  </div>
                  <span>{job.progress_percent}%</span>
                </div>
                <progress
                  max="100"
                  value={job.progress_percent}
                  aria-label={`${job.type} progress`}
                />
                <dl className="processing-job-details">
                  <div>
                    <dt>Stage</dt>
                    <dd>{formatPropertyName(job.stage)}</dd>
                  </div>
                  <div>
                    <dt>Attempt</dt>
                    <dd>{job.attempt_count}</dd>
                  </div>
                  <div>
                    <dt>Queued</dt>
                    <dd>{dateTime(job.available_at)}</dd>
                  </div>
                  <div>
                    <dt>Started</dt>
                    <dd>{dateTime(job.started_at)}</dd>
                  </div>
                  <div>
                    <dt>Finished</dt>
                    <dd>{dateTime(job.finished_at)}</dd>
                  </div>
                </dl>
                {job.error_detail && <p className="processing-job-error">{job.error_detail}</p>}
                {job.can_control && (
                  <div className="processing-job-actions">
                    {job.state === 'FAILED' && job.stage !== 'CANCELLED' && (
                      <button
                        type="button"
                        disabled={busyJobId === job.id}
                        onClick={() => void runAction(job, 'retry')}
                      >
                        Retry
                      </button>
                    )}
                    {job.can_cancel && (
                      <button
                        type="button"
                        disabled={busyJobId === job.id || job.stage === 'CANCELLING'}
                        onClick={() => void runAction(job, 'cancel')}
                      >
                        {job.stage === 'CANCELLING' ? 'Cancelling…' : 'Cancel'}
                      </button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">No processing jobs exist for this revision.</p>
        )}
      </section>
    </div>
  );
}

export function VersionsSection({
  snapshots,
  itemId,
  revisionId,
  canRestore,
}: {
  snapshots: ContentSnapshot[];
  itemId: string;
  revisionId: string;
  canRestore: boolean;
}) {
  return (
    <div className="item-revision-section">
      <div className="item-revision-section-header">
        <div>
          <h2>Version history</h2>
          <p className="muted">
            Immutable Artifact manifests for this revision. Restoring a historical snapshot opens a
            staging session for review and creates a new snapshot on Submit.
          </p>
        </div>
      </div>
      <section className="item-revision-panel version-history-panel">
        {snapshots.length ? (
          <div className="version-history-table-wrap">
            <table className="version-history-table">
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th>Committed</th>
                  <th>Artifacts</th>
                  <th>Commit ID</th>
                  <th>Role</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {snapshots.map((snapshot, index) => {
                  const isCurrent = index === 0;
                  return (
                    <tr key={snapshot.id}>
                      <td>
                        <strong>Snapshot {snapshot.sequence}</strong>
                      </td>
                      <td>{dateTime(snapshot.created_at)}</td>
                      <td>{snapshot.artifact_count}</td>
                      <td className="mono version-hash" title={snapshot.commit_id}>
                        {snapshot.commit_id}
                      </td>
                      <td>
                        {isCurrent ? (
                          <span className="current-version-badge">Current</span>
                        ) : (
                          'Historical'
                        )}
                      </td>
                      <td>
                        {canRestore && !isCurrent && (
                          <Link
                            className="secondary-button"
                            to={
                              `/checkin?mode=edit&itemId=${encodeURIComponent(itemId)}` +
                              `&revisionId=${encodeURIComponent(revisionId)}` +
                              `&sourceSnapshotId=${encodeURIComponent(snapshot.id)}`
                            }
                          >
                            Restore through staging
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            <strong>No snapshots</strong>
            <p className="muted">This revision has no committed Artifact snapshots.</p>
          </div>
        )}
      </section>
    </div>
  );
}
