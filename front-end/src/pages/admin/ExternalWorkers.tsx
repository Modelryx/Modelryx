import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../../api';

type ExternalWorker = {
  id: string;
  url: string;
  enabled: boolean;
  capabilities: {
    cadSystem: string;
    sourceExtensions: string[];
    documentKinds: string[];
    outputRoles: string[];
  };
};

export type ExternalWorkerStatus = {
  id: string;
  cadSystem: string;
  url: string;
  availability: 'ONLINE' | 'OFFLINE' | 'DISABLED' | 'CHANGED';
  checkedAt: string;
  detail: string | null;
};

export function ExternalWorkerAvailability({ workers }: { workers: ExternalWorkerStatus[] }) {
  return (
    <>
      {workers.map((worker) => (
        <article className="worker-health-card" key={`external-${worker.id}`}>
          <div className="maintenance-title-row">
            <strong>
              {worker.cadSystem} · {worker.id}
            </strong>
            <span
              className={`maintenance-health ${worker.availability === 'ONLINE' ? 'healthy' : 'degraded'}`}
            >
              {worker.availability}
            </span>
          </div>
          <dl>
            <div>
              <dt>Endpoint</dt>
              <dd>{worker.url}</dd>
            </div>
            <div>
              <dt>Last checked</dt>
              <dd>
                {worker.availability === 'DISABLED'
                  ? 'Not checked'
                  : new Date(worker.checkedAt).toLocaleString()}
              </dd>
            </div>
          </dl>
          {worker.detail && <p className="maintenance-warning">{worker.detail}</p>}
        </article>
      ))}
    </>
  );
}

export function ExternalWorkers({ onChanged }: { onChanged: () => Promise<void> }) {
  const [workers, setWorkers] = useState<ExternalWorker[]>([]);
  const [id, setId] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function refresh() {
    setWorkers(await api<ExternalWorker[]>('/admin/external-workers'));
  }

  useEffect(() => {
    void refresh().catch((reason) => setError(String(reason)));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(`/admin/external-workers/${encodeURIComponent(id)}`, 'PUT', { url, enabled: true });
      setId('');
      setUrl('');
      await refresh();
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(worker: ExternalWorker) {
    setBusy(true);
    setError('');
    try {
      await api(`/admin/external-workers/${encodeURIComponent(worker.id)}`, 'PUT', {
        url: worker.url,
        enabled: !worker.enabled,
      });
      await refresh();
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function remove(workerId: string) {
    setBusy(true);
    setError('');
    try {
      await api(`/admin/external-workers/${encodeURIComponent(workerId)}`, 'DELETE');
      await refresh();
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="maintenance-card" aria-labelledby="external-worker-title">
      <h3 id="external-worker-title">External CAD workers</h3>
      <p>Register a worker URL. Modelryx verifies its capabilities before accepting it.</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <form className="cad-worker-settings-form" onSubmit={(event) => void save(event)}>
        <label>
          Worker ID
          <input
            required
            pattern="[a-z][a-z0-9_-]{0,63}"
            value={id}
            onChange={(event) => setId(event.target.value)}
          />
        </label>
        <label>
          Worker URL
          <input required type="url" value={url} onChange={(event) => setUrl(event.target.value)} />
        </label>
        <button disabled={busy} type="submit">
          Register or update worker
        </button>
      </form>
      {workers.map((worker) => (
        <div key={worker.id}>
          <strong>{worker.id}</strong> — {worker.capabilities.cadSystem} ({worker.url})
          <p>
            {worker.enabled ? 'Enabled' : 'Disabled'} ·{' '}
            {worker.capabilities.sourceExtensions.join(', ')} ·{' '}
            {worker.capabilities.documentKinds.join(', ')}
          </p>
          <button disabled={busy} type="button" onClick={() => void toggle(worker)}>
            {worker.enabled ? 'Disable' : 'Enable'}
          </button>{' '}
          <button disabled={busy} type="button" onClick={() => void remove(worker.id)}>
            Remove
          </button>
        </div>
      ))}
    </section>
  );
}
