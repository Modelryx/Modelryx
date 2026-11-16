import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, type Revision } from '../api';
import { Button } from '../components/Button';
import { ResourceState, useResource } from '../components/ResourceState';
import { RevisionImpactPanel } from './RevisionImpactPanel';
import './RevisionLifecycle.css';

type Approval = {
  id: string;
  status: string;
  snapshot_id: string;
  can_withdraw: boolean;
  request_comment: string | null;
};
type Group = { id: string; name: string; eligible_member_count: number };
type Snapshot = { id: string; sequence: number };
export type RevisionLifecycleAction = 'approval' | 'emergency' | 'release' | 'withdraw';

const actionLabel: Record<RevisionLifecycleAction, string> = {
  approval: 'Request approval',
  emergency: 'Emergency Review',
  release: 'Release revision',
  withdraw: 'Withdraw review request',
};

type LifecycleProps = {
  itemId: string;
  revision: Revision;
  canEdit: boolean;
  isAdmin: boolean;
  canRelease: boolean;
  isCreator: boolean;
};
const revisionPath = (itemId: string, revisionId: string) =>
  `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`;

export function RevisionLifecycle({
  itemId,
  revision,
  canEdit,
  isAdmin,
  canRelease,
  isCreator,
  closeMenu,
  activeSection,
}: LifecycleProps & { closeMenu: () => void; activeSection: string }) {
  const path = revisionPath(itemId, revision.id);
  const approvals = useResource<Approval[]>(`${path}/approval-requests`);
  const groups = useResource<Group[]>('/approval-groups');
  const versions = useResource<Snapshot[]>(
    `/item-revisions/${encodeURIComponent(revision.id)}/content-snapshots`
  );
  const pending = approvals.data?.find((entry) => entry.status === 'PENDING');
  const eligible =
    revision.lifecycle_state === 'WORKING' &&
    ['READY', 'READY_WITH_WARNINGS'].includes(revision.state);
  const unavailable =
    approvals.loading || !!approvals.error || versions.loading || !!versions.error || !eligible;
  const approved = revision.approval_status === 'APPROVED';
  const choice = (action: RevisionLifecycleAction, enabled: boolean) =>
    enabled ? (
      <Link
        role="menuitem"
        aria-current={activeSection === action ? 'page' : undefined}
        to={`${path}?section=${action}`}
        onClick={closeMenu}
      >
        {actionLabel[action]}
      </Link>
    ) : (
      <button type="button" role="menuitem" disabled>
        {actionLabel[action]}
      </button>
    );
  return (
    <>
      {isCreator && choice(
        'approval',
        canEdit && !unavailable && !pending && !approved && !groups.loading && !groups.error
      )}
      {canRelease && choice('release', !unavailable)}
      {pending?.can_withdraw && choice('withdraw', true)}
      <div className="revision-action-status">
        {approvals.loading ? (
          <span role="status">Loading review status…</span>
        ) : approvals.error ? (
          <span role="alert">
            Review status unavailable.{' '}
            <button type="button" role="menuitem" onClick={approvals.retry}>
              Retry
            </button>
          </span>
        ) : (
          approvals.data && (
            <span>
              {pending
                ? `Review pending for version ${versions.data?.find((row) => row.id === pending.snapshot_id)?.sequence ?? pending.snapshot_id}`
                : approvals.data.length
                  ? `Latest review: ${approvals.data[0].status}`
                  : 'No review requests.'}
            </span>
          )
        )}
        {versions.error && (
          <span role="alert">
            Content versions unavailable.{' '}
            <button type="button" role="menuitem" onClick={versions.retry}>
              Retry
            </button>
          </span>
        )}
        {groups.error && (
          <span role="alert">
            Reviewer groups unavailable.{' '}
            <button type="button" role="menuitem" onClick={groups.retry}>
              Retry
            </button>
          </span>
        )}
      </div>
    </>
  );
}

export function RevisionLifecyclePanel({
  itemId,
  revision,
  canEdit,
  isAdmin,
  canRelease,
  isCreator,
  action,
  onChanged,
}: LifecycleProps & { action: RevisionLifecycleAction; onChanged: () => void }) {
  const path = revisionPath(itemId, revision.id);
  const approvals = useResource<Approval[]>(`${path}/approval-requests`);
  const groups = useResource<Group[]>(action === 'approval' ? '/approval-groups' : null);
  const versions = useResource<Snapshot[]>(
    `/item-revisions/${encodeURIComponent(revision.id)}/content-snapshots`
  );
  const [group, setGroup] = useState('');
  const [comment, setComment] = useState('');
  const [incident, setIncident] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [completed, setCompleted] = useState(false);
  const pending = approvals.data?.find((entry) => entry.status === 'PENDING');
  const current = versions.data?.reduce<Snapshot | undefined>(
    (latest, row) => (!latest || row.sequence > latest.sequence ? row : latest),
    undefined
  );
  const eligible =
    revision.lifecycle_state === 'WORKING' &&
    ['READY', 'READY_WITH_WARNINGS'].includes(revision.state);
  const permitted =
    action === 'withdraw'
      ? !!pending?.can_withdraw
      : action === 'emergency'
        ? canEdit && isAdmin && eligible && !pending && revision.approval_status !== 'APPROVED'
        : action === 'approval'
          ? isCreator && canEdit && eligible && !pending && revision.approval_status !== 'APPROVED'
          : canRelease && eligible;
  const ready =
    !approvals.loading &&
    !approvals.error &&
    !versions.loading &&
    !versions.error &&
    (action !== 'approval' || (!groups.loading && !groups.error));

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || completed || !ready || !permitted || (action !== 'withdraw' && !current)) return;
    let endpoint = '';
    let body: unknown;
    if (action === 'approval') {
      endpoint = `${path}/approval-requests`;
      body = { comment: comment.trim() || null, reviewer_group_id: group || null };
    }
    if (action === 'emergency') {
      if (!incident.trim() || !comment.trim()) return;
      endpoint = `${path}/approval-requests`;
      body = { comment: comment.trim(), emergency: true, incident_reference: incident.trim() };
    }
    if (action === 'release') endpoint = `${path}/release`;
    if (action === 'withdraw')
      endpoint = `/approval-requests/${encodeURIComponent(pending!.id)}/withdraw`;
    setBusy(true);
    setError('');
    try {
      await api(endpoint, 'POST', body);
      setCompleted(true);
      approvals.retry();
      versions.retry();
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update revision lifecycle');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="lifecycle-action-page" aria-labelledby="lifecycle-action-title">
      <header>
        <span>Revision action</span>
        <h2 id="lifecycle-action-title">{actionLabel[action]}</h2>
        <p>
          Revision {revision.revision_code} · {revision.state.replaceAll('_', ' ')}
        </p>
      </header>
      <ResourceState state={approvals} />
      <ResourceState state={versions} />
      {action === 'approval' && <ResourceState state={groups} />}
      {completed ? (
        <div className="lifecycle-action-result" role="status">
          <strong>
            {action === 'release'
              ? 'Revision released.'
              : action === 'withdraw'
                ? 'Review request withdrawn.'
                : 'Review request submitted.'}
          </strong>
          <p>The revision status and review history have been refreshed.</p>
          <Link to={path}>Return to overview</Link>
        </div>
      ) : (
        <form className="lifecycle-action-form" onSubmit={(event) => void submit(event)}>
          <div className="lifecycle-action-context">
            <span>Current content version</span>
            <strong>{current ? `Version ${current.sequence}` : 'Unavailable'}</strong>
          </div>
          {action !== 'withdraw' && current && (
            <RevisionImpactPanel itemId={itemId} revisionId={revision.id} snapshotId={current.id} />
          )}
          {action === 'approval' && (
            <>
              <p>
                The request is tied to the current content version. Reviewers will assess this exact
                version.
              </p>
              <label className="dialog-field">
                Reviewer group
                <select value={group} onChange={(event) => setGroup(event.target.value)}>
                  <option value="">Any eligible reviewer</option>
                  {groups.data?.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.name} ({entry.eligible_member_count})
                    </option>
                  ))}
                </select>
              </label>
              <label className="dialog-field">
                What should reviewers verify?
                <textarea
                  rows={4}
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                />
              </label>
            </>
          )}
          {action === 'emergency' && (
            <>
              <p>
                Emergency review requires an incident or change reference and a reason for the
                urgency. The request applies to the current content version.
              </p>
              <label className="dialog-field">
                Incident or change reference
                <input
                  required
                  value={incident}
                  onChange={(event) => setIncident(event.target.value)}
                />
              </label>
              <label className="dialog-field">
                Reason for emergency review
                <textarea
                  required
                  rows={4}
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                />
              </label>
            </>
          )}
          {action === 'release' && (
            <p>
              Release is a controlled lifecycle transition. The server verifies approval,
              permissions, and the current revision state before applying it.
            </p>
          )}
          {action === 'withdraw' && (
            <p>
              Withdraw the pending review for version{' '}
              {versions.data?.find((row) => row.id === pending?.snapshot_id)?.sequence ??
                pending?.snapshot_id ??
                '—'}
              . Reviewers will no longer be able to decide this request.
            </p>
          )}
          {ready && !permitted && (
            <p className="lifecycle-action-unavailable" role="status">
              This action is not available for the current revision state, review status, or your
              permissions.
            </p>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <Link className="button button--secondary button-link" to={path}>
              Cancel
            </Link>
            <Button
              type="submit"
              disabled={
                busy ||
                !ready ||
                !permitted ||
                (action !== 'withdraw' && !current) ||
                (action === 'emergency' && (!incident.trim() || !comment.trim()))
              }
            >
              {busy
                ? 'Saving…'
                : action === 'emergency'
                  ? 'Request emergency review'
                  : actionLabel[action]}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
