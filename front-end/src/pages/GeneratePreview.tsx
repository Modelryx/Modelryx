import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type Item, type Revision } from '../api';
import { Button } from '../components/Button';
import { ResourceState, useResource } from '../components/ResourceState';
import type { StageProcessingJob } from './Stages';
import './GeneratePreview.css';

type Snapshot = { id: string; sequence: number; created_at: string };
type GenerationJob = StageProcessingJob & {
  snapshot_id: string | null;
  diagnostics_json: Record<string, unknown> | null;
};
type ProcessingFile = StageProcessingJob['files'][number];

const isActive = (job: GenerationJob | undefined) =>
  job?.state === 'PENDING' || job?.state === 'RUNNING';
const label = (value: string) => value.replaceAll('_', ' ').toLowerCase();
const when = (value: string | null) => (value ? new Date(value).toLocaleString() : '—');
const errorText = (reason: unknown, fallback: string) =>
  reason instanceof Error ? reason.message : fallback;

function warningMessages(job: GenerationJob | undefined): string[] {
  const values = job?.diagnostics_json?.warnings;
  if (!Array.isArray(values)) return [];
  return values.flatMap((value) => {
    if (typeof value === 'string') return value.trim() ? [value.trim()] : [];
    if (
      value &&
      typeof value === 'object' &&
      'message' in value &&
      typeof value.message === 'string'
    )
      return value.message.trim() ? [value.message.trim()] : [];
    return [];
  });
}

function FileResult({ file, jobWarnings }: { file: ProcessingFile; jobWarnings: string[] }) {
  const fileMessage = file.message?.trim();
  const showJobWarnings = jobWarnings.length > 0 && jobWarnings.join(' ') !== fileMessage;
  return (
    <li>
      <div>
        <strong>{file.logical_path || file.filename}</strong>
        <span>
          {file.role || 'CAD source'}
          {file.stage ? ` · ${label(file.stage)}` : ''}
        </span>
      </div>
      <span
        className={`generation-file-status generation-file-status--${file.status.toLowerCase()}`}
      >
        {label(file.status)}
      </span>
      {file.status === 'WARNING' ? (
        <details className="generation-file-warning">
          <summary>View warning details</summary>
          {fileMessage && (
            <p>
              <strong>File message:</strong> {fileMessage}
            </p>
          )}
          {showJobWarnings && (
            <>
              <p>
                <strong>Processing job warnings:</strong>
              </p>
              <ul>
                {jobWarnings.map((warning, index) => (
                  <li key={`${index}:${warning}`}>{warning}</li>
                ))}
              </ul>
            </>
          )}
          {!fileMessage && !showJobWarnings && (
            <p>
              The processor marked this file with a warning but did not provide a detailed message.
            </p>
          )}
          {file.fix_suggestion && (
            <p>
              <strong>Suggested fix:</strong> {file.fix_suggestion}
            </p>
          )}
        </details>
      ) : (
        <>
          {file.message && <p>{file.message}</p>}
          {file.fix_suggestion && <p>{file.fix_suggestion}</p>}
        </>
      )}
    </li>
  );
}

export function GeneratePreviewPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { itemId = '', revisionId = '' } = useParams();
  const revisionPath = `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`;
  const item = useResource<Item>(itemId ? `/items/${encodeURIComponent(itemId)}` : null);
  const revision = useResource<Revision>(revisionId ? revisionPath : null);
  const snapshots = useResource<Snapshot[]>(
    revisionId ? `/item-revisions/${encodeURIComponent(revisionId)}/content-snapshots` : null
  );
  const [jobs, setJobs] = useState<GenerationJob[] | null>(null);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobsError, setJobsError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [actionError, setActionError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const submittingRef = useRef(false);
  const mountedRef = useRef(true);
  const revisionRef = useRef(revisionId);
  revisionRef.current = revisionId;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setJobs(null);
    setJobsLoading(true);
    setJobsError('');
    setStatusError('');
    api<GenerationJob[]>(`/revisions/${encodeURIComponent(revisionId)}/processing-jobs`)
      .then((result) => {
        if (live) setJobs(result);
      })
      .catch((reason) => {
        if (live) setJobsError(errorText(reason, 'Unable to load processing jobs.'));
      })
      .finally(() => {
        if (live) setJobsLoading(false);
      });
    return () => {
      live = false;
    };
  }, [revisionId, reload]);

  const current = snapshots.data?.reduce<Snapshot | undefined>(
    (latest, row) => (!latest || row.sequence > latest.sequence ? row : latest),
    undefined
  );
  const generationJobs = jobs?.filter((job) => job.type.endsWith('_TO_MODELRYX')) ?? [];
  const job = current
    ? generationJobs.find((entry) => entry.snapshot_id === current.id)
    : undefined;
  const previousJob = !job ? generationJobs[0] : undefined;
  const running = isActive(job);

  useEffect(() => {
    if (!job || !isActive(job)) return;
    let live = true;
    let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      api<GenerationJob>(`/processing-jobs/${encodeURIComponent(job.id)}`)
        .then((next) => {
          if (!live) return;
          setJobs((rows) => rows?.map((row) => (row.id === next.id ? next : row)) ?? [next]);
          setStatusError('');
          if (!isActive(next)) {
            snapshots.retry();
            revision.retry();
            setReload((value) => value + 1);
          }
        })
        .catch((reason) => {
          if (live) setStatusError(errorText(reason, 'Unable to refresh processing status.'));
        })
        .finally(() => {
          inFlight = false;
        });
    }, 2500);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.state]);

  const submit = async (retry: boolean) => {
    if (submittingRef.current || jobsLoading || snapshots.loading || running || !current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setActionError('');
    try {
      const path =
        retry && job
          ? `/processing-jobs/${encodeURIComponent(job.id)}/retry`
          : `/revisions/${encodeURIComponent(revisionId)}/preview/reprocess`;
      const next = await api<GenerationJob>(path, 'POST');
      if (mountedRef.current && revisionRef.current === revisionId) {
        setJobs((rows) => [next, ...(rows ?? []).filter((row) => row.id !== next.id)]);
        setStatusError('');
        snapshots.retry();
      }
    } catch (reason) {
      if (mountedRef.current && revisionRef.current === revisionId) {
        setActionError(errorText(reason, 'Unable to queue generation.'));
        snapshots.retry();
        revision.retry();
        setReload((value) => value + 1);
      }
    } finally {
      submittingRef.current = false;
      if (mountedRef.current && revisionRef.current === revisionId) setSubmitting(false);
    }
  };

  const retryFailed = job?.state === 'FAILED' && job.can_control;
  const knownControl = generationJobs[0]?.can_control;
  const canRequest = knownControl !== false;
  const showStatus = !!job && !jobsLoading && !jobsError;
  return (
    <div className="generation-page">
      {!embedded && (
        <p className="breadcrumbs">
          <Link to="/library">Library</Link> /{' '}
          <Link to={`/items/${encodeURIComponent(itemId)}`}>
            {item.data?.item_number ?? 'Item'}
          </Link>{' '}
          / <Link to={revisionPath}>Revision {revision.data?.revision_code ?? ''}</Link> / Generate
          metadata &amp; preview
        </p>
      )}
      <header className="generation-header">
        <div>
          <span className="generation-eyebrow">Revision processing</span>
          {embedded ? (
            <h2>Generate metadata &amp; preview</h2>
          ) : (
            <h1>Generate metadata &amp; preview</h1>
          )}
          <p>Process the current committed CAD content and track the result for this revision.</p>
        </div>
        {!embedded && (
          <Link className="button button--secondary button-link" to={revisionPath}>
            Back to revision
          </Link>
        )}
      </header>
      <ResourceState state={item} />
      <ResourceState state={revision} />
      <ResourceState state={snapshots} />
      {jobsLoading && (
        <p className="generation-notice" role="status">
          Checking existing generation jobs…
        </p>
      )}
      {jobsError && (
        <div className="generation-notice generation-notice--error" role="alert">
          <span>{jobsError}</span>
          <Button
            variant="secondary"
            onClick={() => {
              snapshots.retry();
              setReload((value) => value + 1);
            }}
          >
            Retry status check
          </Button>
        </div>
      )}
      {!snapshots.loading && !snapshots.error && !current && (
        <p className="generation-notice">
          This revision has no committed content version. Commit a supported CAD source before
          generating metadata and preview.
        </p>
      )}
      {current && !jobsLoading && !jobsError && (
        <>
          <div className="generation-layout">
            <section
              className="generation-card generation-main"
              aria-labelledby="generation-current-title"
            >
              <div className="generation-card-heading">
                <div>
                  <span className="generation-eyebrow">
                    Current content · Version {current.sequence}
                  </span>
                  <h2 id="generation-current-title">
                    {showStatus
                      ? job.state === 'SUCCEEDED'
                        ? 'Generation finished'
                        : job.state === 'FAILED'
                          ? 'Generation failed'
                          : job.state === 'PENDING'
                            ? 'Waiting for a worker'
                            : 'Generation in progress'
                      : 'Ready to generate'}
                  </h2>
                </div>
                {showStatus && (
                  <span className={`generation-badge generation-badge--${job.state.toLowerCase()}`}>
                    {label(job.state)}
                  </span>
                )}
              </div>
              {showStatus ? (
                <>
                  <p className="generation-status-copy" role="status">
                    {job.state === 'SUCCEEDED'
                      ? 'Processing completed for this content version. Review the generated properties and available representations below.'
                      : job.state === 'FAILED'
                        ? 'Processing stopped before completion. Review the error and suggested fix before retrying.'
                        : job.state === 'PENDING'
                          ? 'The request is queued. This page checks its status automatically while you stay here.'
                          : 'The CAD worker is processing this version. This page checks its status automatically while you stay here.'}
                  </p>
                  <div className="generation-progress">
                    <div>
                      <strong>Progress</strong>
                      <span>{job.progress_percent}%</span>
                    </div>
                    <progress
                      max={100}
                      value={job.progress_percent}
                      aria-label="Generation progress"
                    />
                  </div>
                  <dl className="generation-details">
                    <div>
                      <dt>Current stage</dt>
                      <dd>{label(job.stage)}</dd>
                    </div>
                    <div>
                      <dt>Processing type</dt>
                      <dd>{label(job.type)}</dd>
                    </div>
                    <div>
                      <dt>Queued</dt>
                      <dd>{when(job.available_at)}</dd>
                    </div>
                    <div>
                      <dt>Started</dt>
                      <dd>{when(job.started_at)}</dd>
                    </div>
                    <div>
                      <dt>{job.state === 'FAILED' ? 'Stopped' : 'Finished'}</dt>
                      <dd>{when(job.finished_at)}</dd>
                    </div>
                    <div>
                      <dt>Attempt</dt>
                      <dd>{job.attempt_count}</dd>
                    </div>
                    <div>
                      <dt>Job ID</dt>
                      <dd>{job.id}</dd>
                    </div>
                  </dl>
                  {job.state === 'FAILED' && (
                    <div className="generation-failure" role="alert">
                      <strong>
                        {job.error_detail || 'The CAD worker did not complete processing.'}
                      </strong>
                      {job.fix_suggestion && <p>{job.fix_suggestion}</p>}
                    </div>
                  )}
                  {job.state === 'SUCCEEDED' && (
                    <div className="generation-result-links">
                      <Link
                        className="button button--primary button-link"
                        to={`${revisionPath}?section=properties`}
                      >
                        View properties
                      </Link>
                      <Link
                        className="button button--secondary button-link"
                        to={`${revisionPath}?section=preview`}
                      >
                        View preview
                      </Link>
                    </div>
                  )}
                  {statusError && (
                    <div className="generation-notice generation-notice--error" role="alert">
                      <span>{statusError}</span>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          snapshots.retry();
                          setReload((value) => value + 1);
                        }}
                      >
                        Refresh status
                      </Button>
                    </div>
                  )}
                </>
              ) : (
                <p className="generation-status-copy">
                  No generation job exists for this content version. Starting one queues processing
                  of the current committed source.
                </p>
              )}
              {previousJob && (
                <p className="generation-prior">
                  The latest job belongs to an earlier content version ({label(previousJob.state)}).
                  Start a new job for Version {current.sequence}.
                </p>
              )}
              <div className="generation-actions">
                <Button
                  disabled={submitting || running || !canRequest || !item.data || !revision.data}
                  onClick={() => void submit(!!retryFailed)}
                >
                  {submitting
                    ? 'Queueing…'
                    : running
                      ? 'Processing…'
                      : retryFailed
                        ? 'Retry failed generation'
                        : job?.state === 'SUCCEEDED'
                          ? 'Generate again'
                          : 'Start generation'}
                </Button>
                {running && <span>Updates every few seconds while this page is open.</span>}
              </div>
              <p className="generation-permission">
                Only the item creator or an administrator can request processing.
                {!canRequest ? ' Your account cannot control this job.' : ''}
              </p>
              {actionError && (
                <p className="generation-action-error" role="alert">
                  {actionError}
                </p>
              )}
            </section>
            <aside className="generation-card" aria-labelledby="generation-process-title">
              <span className="generation-eyebrow">What happens</span>
              <h2 id="generation-process-title">Processing flow</h2>
              <ol className="generation-steps">
                <li>
                  <strong>Read committed source</strong>
                  <span>
                    Modelryx selects a supported native CAD file from the current content version.
                  </span>
                </li>
                <li>
                  <strong>Inspect and convert</strong>
                  <span>
                    A compatible worker extracts engineering data and prepares viewable output.
                  </span>
                </li>
                <li>
                  <strong>Validate and publish</strong>
                  <span>
                    The worker checks its output, then publishes metadata and available preview
                    representations.
                  </span>
                </li>
              </ol>
              <p className="generation-scope">
                This does not change the native CAD source or create a new revision. Availability of
                each preview depends on the source and worker result.
              </p>
            </aside>
          </div>
          {job?.files.length ? (
            <section
              className="generation-card generation-files"
              aria-labelledby="generation-files-title"
            >
              <div className="generation-card-heading">
                <div>
                  <span className="generation-eyebrow">Source files</span>
                  <h2 id="generation-files-title">File results</h2>
                </div>
                <span>{job.files.length} files</span>
              </div>
              <ul>
                {job.files.map((file) => (
                  <FileResult key={file.id} file={file} jobWarnings={warningMessages(job)} />
                ))}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
