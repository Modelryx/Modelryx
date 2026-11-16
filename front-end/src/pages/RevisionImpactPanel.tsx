import { Link } from 'react-router-dom';
import { ResourceState, useResource } from '../components/ResourceState';

type ProcessingJob = {
  id: string;
  snapshot_id: string | null;
  type: string;
  state: string;
  stage: string;
  error_detail: string | null;
  diagnostics_json: { warnings?: unknown[] } | null;
};
type WhereUsed = {
  items: Array<{
    parent_item: { id: string; item_number: string };
    parent_revision: { id: string; revision_code: string };
    direct: boolean;
  }>;
  warnings: string[];
};

export function RevisionImpactPanel({
  itemId,
  revisionId,
  snapshotId,
}: {
  itemId: string;
  revisionId: string;
  snapshotId?: string;
}) {
  const jobs = useResource<ProcessingJob[]>(
    `/revisions/${encodeURIComponent(revisionId)}/processing-jobs`
  );
  const whereUsed = useResource<WhereUsed>(
    `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}/where-used`
  );
  const relevantJobs = (jobs.data ?? []).filter(
    (job) => !snapshotId || job.snapshot_id === snapshotId
  );
  // The API returns newest jobs first. A retry supersedes older attempts of the same type.
  const latestJobs = [...new Map(
    [...relevantJobs].reverse().map((job) => [job.type, job])
  ).values()];
  const active = latestJobs.filter((job) => ['PENDING', 'RUNNING'].includes(job.state));
  const failed = latestJobs.filter((job) => job.state === 'FAILED');
  const parents = new Map(
    (whereUsed.data?.items ?? []).map((row) => [row.parent_revision.id, row])
  );
  const base = `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`;
  return (
    <section className="revision-impact-panel" aria-label="Engineering impact before action">
      <h3>Before you continue</h3>
      <p>
        This is a read-only summary. The server checks permissions, approval, and revision state
        when you submit.
      </p>
      <ResourceState state={jobs} />
      <ResourceState state={whereUsed} />
      {!jobs.loading && !jobs.error && (
        <div>
          <strong>Processing</strong>
          <span>
            {active.length} active · {failed.length} failed for this content version
          </span>
          <Link to={`${base}?section=stages`}>View jobs</Link>
        </div>
      )}
      {failed.slice(0, 3).map((job) => (
        <p className="revision-impact-warning" key={job.id}>
          {job.type.replaceAll('_', ' ')}: {job.error_detail || 'Processing failed'}
        </p>
      ))}
      {latestJobs
        .flatMap((job) => job.diagnostics_json?.warnings ?? [])
        .slice(0, 3)
        .map((warning, index) => (
          <p className="revision-impact-notice" key={`warning-${index}`}>
            {typeof warning === 'string' ? warning : JSON.stringify(warning)}
          </p>
        ))}
      {!whereUsed.loading && !whereUsed.error && (
        <div>
          <strong>Referenced by</strong>
          <span>
            {parents.size} parent revision{parents.size === 1 ? '' : 's'}
          </span>
          <Link to={`${base}?section=relationships`}>View where used</Link>
        </div>
      )}
      {[...parents.values()].slice(0, 5).map((row) => (
        <p key={row.parent_revision.id}>
          <Link
            to={`/items/${encodeURIComponent(row.parent_item.id)}/revisions/${encodeURIComponent(row.parent_revision.id)}`}
          >
            {row.parent_item.item_number} · Revision {row.parent_revision.revision_code}
          </Link>{' '}
          {row.direct ? 'direct' : 'indirect'}
        </p>
      ))}
      {(whereUsed.data?.warnings ?? []).slice(0, 3).map((warning, index) => (
        <p className="revision-impact-warning" key={`where-used-${index}`}>
          {warning}
        </p>
      ))}
    </section>
  );
}
