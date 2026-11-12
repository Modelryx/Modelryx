import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../workflow-api';
import { randomUUID } from '../uuid';

type CADSession = {
  id: string;
  revision_id: string;
  status: string;
  source_sha256: string;
};

type CADPlan = {
  operation: string;
  target_id: string;
  parameter: string;
  expected_value: unknown;
  new_value: unknown;
  unit: string | null;
};

type CADExecution = {
  result: {
    output_sha256?: string;
    comparison?: { changed?: Record<string, { before?: unknown; after?: unknown }> };
    validation?: { engine?: string; passed?: boolean; checks?: string[] };
    diagnostics?: { warnings?: string[] };
  };
};

type CADResult = { session: CADSession; plan: CADPlan; execution: CADExecution };
type CommitPlan = {
  id: string;
  status: string;
  operation: 'NEW_VERSION' | 'NEW_REVISION';
  revision_code: string;
  description: string | null;
  preview: Record<string, unknown>;
};

function engineeringValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

export function CADReviewPage() {
  const { itemId, revisionId, sessionId } = useParams();
  const navigate = useNavigate();
  const [result, setResult] = useState<CADResult>();
  const [commitPlan, setCommitPlan] = useState<CommitPlan>();
  const [revisionCode, setRevisionCode] = useState('');
  const [revisionDescription, setRevisionDescription] = useState('');
  const [publishOperation, setPublishOperation] = useState<'NEW_VERSION' | 'NEW_REVISION'>(
    'NEW_VERSION'
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!sessionId) return;
    api<CADResult>(`/cad-sessions/${sessionId}/result`)
      .then(setResult)
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : 'Unable to load CAD result')
      );
  }, [sessionId]);

  const discard = async () => {
    if (!sessionId || !confirm('Discard this verified CAD result? This cannot be undone.')) return;
    setBusy(true);
    setError('');
    try {
      await api(`/cad-sessions/${sessionId}/discard`, 'POST');
      navigate(`/items/${itemId}/revisions/${revisionId}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Discard failed');
    } finally {
      setBusy(false);
    }
  };

  const prepareCommit = async () => {
    if (!itemId || !sessionId) return;
    setBusy(true);
    setError('');
    try {
      const proposal = await api<CommitPlan>(`/cad-sessions/${sessionId}/commit-plans`, 'POST');
      setCommitPlan(proposal);
      setRevisionCode(proposal.revision_code);
      setRevisionDescription(proposal.description ?? '');
      setPublishOperation('NEW_VERSION');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to prepare revision');
    } finally {
      setBusy(false);
    }
  };

  const confirmCommit = async () => {
    if (!commitPlan || !itemId || (publishOperation === 'NEW_REVISION' && !revisionCode.trim()))
      return;
    const resultLabel =
      publishOperation === 'NEW_VERSION'
        ? 'a new version of the selected revision'
        : `new revision ${revisionCode.trim()}`;
    if (!confirm(`Create ${resultLabel} from this verified CAD result?`)) return;
    setBusy(true);
    setError('');
    try {
      const committed = await api<{ revision_id: string }>(
        `/cad-commit-plans/${commitPlan.id}/confirm`,
        'POST',
        {
          confirmed: true,
          operation: publishOperation,
          revision_code: revisionCode.trim(),
          description: revisionDescription.trim() || null,
          idempotency_key: `cad-ui-${randomUUID()}`,
        }
      );
      navigate(`/items/${itemId}/revisions/${committed.revision_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Revision commit failed');
    } finally {
      setBusy(false);
    }
  };

  if (!result && !error)
    return (
      <section className="page">
        <p>Loading CAD result…</p>
      </section>
    );
  const changed = result?.execution.result.comparison?.changed ?? {};
  return (
    <section className="page">
      <p>
        <Link to={`/items/${itemId}/revisions/${revisionId}`}>← Return to source revision</Link>
      </p>
      <h1>Review CAD result</h1>
      <p className="page-subtitle">
        A verified working copy remains uncommitted until the second confirmation.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <>
          <section className="item-revision-panel">
            <h2>Requested change</h2>
            <dl className="revision-properties">
              <div>
                <dt>Operation</dt>
                <dd>{result.plan.operation}</dd>
              </div>
              <div>
                <dt>Target</dt>
                <dd>
                  {result.plan.target_id}.{result.plan.parameter}
                </dd>
              </div>
              <div>
                <dt>Before</dt>
                <dd>
                  <pre>{engineeringValue(result.plan.expected_value)}</pre>
                </dd>
              </div>
              <div>
                <dt>After</dt>
                <dd>
                  <pre>{engineeringValue(result.plan.new_value)}</pre>
                </dd>
              </div>
              <div>
                <dt>Validation</dt>
                <dd>{result.execution.result.validation?.passed ? 'Passed' : 'Failed'}</dd>
              </div>
              <div>
                <dt>Output SHA-256</dt>
                <dd>{result.execution.result.output_sha256 ?? '—'}</dd>
              </div>
            </dl>
          </section>
          <section className="item-revision-panel">
            <h2>Observed engineering changes</h2>
            {Object.keys(changed).length ? (
              <dl className="revision-properties">
                {Object.entries(changed).map(([name, value]) => (
                  <div key={name}>
                    <dt>{name.replaceAll('_', ' ')}</dt>
                    <dd>
                      <pre>
                        {engineeringValue(value.before)} → {engineeringValue(value.after)}
                      </pre>
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="muted">No summarized engineering facts changed.</p>
            )}
          </section>
          {commitPlan && (
            <section className="item-revision-panel">
              <h2>Publish CAD result</h2>
              <fieldset>
                <legend>Result type</legend>
                <label>
                  <input
                    type="radio"
                    name="cad-publish-operation"
                    checked={publishOperation === 'NEW_VERSION'}
                    onChange={() => setPublishOperation('NEW_VERSION')}
                  />{' '}
                  Create new version
                </label>
                <p className="muted">
                  Add the next immutable version to the selected revision. The Item&apos;s current
                  revision does not change.
                </p>
                <label>
                  <input
                    type="radio"
                    name="cad-publish-operation"
                    checked={publishOperation === 'NEW_REVISION'}
                    onChange={() => setPublishOperation('NEW_REVISION')}
                  />{' '}
                  Create new revision
                </label>
                <p className="muted">
                  Create version 1 of a new revision and make it the Item&apos;s current revision.
                </p>
              </fieldset>
              {publishOperation === 'NEW_REVISION' && (
                <>
                  <p className="muted">
                    The suggested code follows the latest revision on the item. You may choose
                    another unused code.
                  </p>
                  <label htmlFor="cad-revision-code">Revision code</label>
                  <input
                    id="cad-revision-code"
                    value={revisionCode}
                    maxLength={50}
                    pattern="[A-Za-z0-9._-]+"
                    onChange={(event) => setRevisionCode(event.target.value)}
                  />
                  <label htmlFor="cad-revision-description">Description</label>
                  <textarea
                    id="cad-revision-description"
                    value={revisionDescription}
                    maxLength={10000}
                    rows={4}
                    placeholder="Describe the purpose and engineering intent of this revision"
                    onChange={(event) => setRevisionDescription(event.target.value)}
                  />
                </>
              )}
            </section>
          )}
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem' }}>
            <button type="button" disabled={busy} onClick={discard}>
              Discard
            </button>
            {!commitPlan ? (
              <button type="button" disabled={busy} onClick={prepareCommit}>
                Prepare new Work version/revision
              </button>
            ) : (
              <button
                type="button"
                disabled={busy || (publishOperation === 'NEW_REVISION' && !revisionCode.trim())}
                onClick={confirmCommit}
              >
                {publishOperation === 'NEW_VERSION'
                  ? 'Confirm new version'
                  : `Confirm revision ${revisionCode.trim() || '—'}`}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
