import { Link } from 'react-router-dom';
import type { CheckinController } from './useCheckinWorkflowController';

export function CommittedItemView({ controller }: { controller: CheckinController }) {
  const {
    commitMessage,
    committedItemId,
    committedRevisionId,
    commitProcessingJobs,
    commitProcessingLoaded,
    commitProcessingError,
  } = controller;
  if (!committedItemId) return null;

  const committedUrl = committedRevisionId
    ? `/items/${committedItemId}/revisions/${committedRevisionId}`
    : `/items/${committedItemId}`;
  return (
    <section className="page checkin-new-page">
      <div className="checkin-header">
        <div>
          <span className="checkin-eyebrow">Commit complete</span>
          <h1>New Item committed</h1>
          <p className="page-subtitle">
            {commitMessage || 'The files were committed successfully.'}
          </p>
        </div>
      </div>
      <section className="checkin-plan-confirmed" role="status">
        <h2>Commit saved</h2>
        <p>The committed files are now read-only in this staging session.</p>
        <div className="commit-processing-status" aria-live="polite">
          <div className="commit-processing-heading">
            <div>
              <h3>Metadata and preview processing</h3>
              {!commitProcessingLoaded && <span className="status-badge checking">Starting…</span>}
              {commitProcessingLoaded && commitProcessingJobs.length === 0 && (
                <span className="status-badge checking">Queued…</span>
              )}
              {commitProcessingJobs.length > 0 &&
                commitProcessingJobs.every((job) => job.state === 'SUCCEEDED') && (
                  <span className="status-badge complete">Done</span>
                )}
              {commitProcessingJobs.some((job) => job.state === 'FAILED') && (
                <span className="status-badge error">Failed</span>
              )}
              {commitProcessingJobs.some((job) => ['PENDING', 'RUNNING'].includes(job.state)) && (
                <span className="status-badge uploading">Processing</span>
              )}
            </div>
            <small>Status updates automatically</small>
          </div>
          {commitProcessingError && (
            <p className="commit-processing-error" role="alert">
              Status temporarily unavailable: {commitProcessingError}. Retrying automatically.
            </p>
          )}
          {commitProcessingJobs.map((job) => (
            <article className="commit-processing-job" key={job.id}>
              <div>
                <strong>{job.type.replaceAll('_', ' ')}</strong>
                <span>{job.progress_percent}%</span>
              </div>
              <progress
                max="100"
                value={job.progress_percent}
                aria-label={`${job.type.replaceAll('_', ' ')} progress`}
              />
              <p>
                {job.state === 'SUCCEEDED'
                  ? 'Done'
                  : job.state === 'FAILED'
                    ? 'Failed'
                    : job.stage.replaceAll('_', ' ')}
                {job.attempt_count > 1 ? ` · Attempt ${job.attempt_count}` : ''}
              </p>
              {job.error_detail && <p className="commit-processing-error">{job.error_detail}</p>}
            </article>
          ))}
        </div>
        <div className="checkin-footer-actions">
          <Link className="button-primary" to={committedUrl}>
            Open committed Item
          </Link>
          <Link className="button-secondary" to="/checkin" reloadDocument>
            Create another Item
          </Link>
          <Link className="button-secondary" to="/library">
            Return to Library
          </Link>
        </div>
      </section>
    </section>
  );
}
