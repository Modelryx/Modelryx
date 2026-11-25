import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api';
import {
  ExternalWorkers,
  ExternalWorkerAvailability,
  type ExternalWorkerStatus,
} from './ExternalWorkers';
import type { Dispatch, FormEvent, SetStateAction } from 'react';
import type { CadWorkerSettings, CadWorkerStatus, CheckoutNotificationRun, Status } from './types';

const formatBytes = (bytes: number) => `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;

export type OperationsPanelProps = {
  status: Status | null;
  cadWorkerStatus: CadWorkerStatus | null;
  cadWorkerSettings: CadWorkerSettings;
  setCadWorkerSettings: Dispatch<SetStateAction<CadWorkerSettings>>;
  setCadWorkerSettingsSaved: Dispatch<SetStateAction<boolean>>;
  cadWorkerSettingsBusy: boolean;
  cadWorkerStatusBusy: boolean;
  cadWorkerSettingsSaved: boolean;
  loading: boolean;
  notificationBusy: boolean;
  notificationRun: CheckoutNotificationRun | null;
  onSaveSettings: (event: FormEvent<HTMLFormElement>) => void;
  onRefresh: () => Promise<void>;
  onRunNotifications: () => Promise<void>;
};

export function OperationsPanel({
  status,
  cadWorkerStatus,
  cadWorkerSettings,
  setCadWorkerSettings,
  setCadWorkerSettingsSaved,
  cadWorkerSettingsBusy,
  cadWorkerStatusBusy,
  cadWorkerSettingsSaved,
  loading,
  notificationBusy,
  notificationRun,
  onSaveSettings,
  onRefresh,
  onRunNotifications,
}: OperationsPanelProps) {
  const [externalStatuses, setExternalStatuses] = useState<ExternalWorkerStatus[]>([]);
  const [externalStatusError, setExternalStatusError] = useState('');
  const refreshExternalStatuses = useCallback(async () => {
    try {
      setExternalStatuses(await api<ExternalWorkerStatus[]>('/admin/external-workers/status'));
      setExternalStatusError('');
    } catch (reason) {
      setExternalStatusError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);
  useEffect(() => {
    void refreshExternalStatuses();
  }, [refreshExternalStatuses]);
  return (
    <div className="admin-tab-panel">
      <div className="admin-section" style={{ padding: '1.25rem' }}>
        <h2>Operations</h2>
        <div className="admin-stats">
          <div>
            <span>System</span>
            <strong>{status?.status ?? '—'}</strong>
          </div>
          <div>
            <span>Queue depth</span>
            <strong>{status?.queue_depth ?? '—'}</strong>
          </div>
          <div>
            <span>Active staging</span>
            <strong>{status?.active_staging_sessions ?? '—'}</strong>
          </div>
          <div>
            <span>Stored artifacts</span>
            <strong>{status ? formatBytes(status.verified_artifact_bytes) : '—'}</strong>
          </div>
          <div>
            <span>Storage free</span>
            <strong>{status ? formatBytes(status.storage_free_bytes) : '—'}</strong>
          </div>
          <div>
            <span>Checkout notifications</span>
            <strong>{status?.checkout_notification_health ?? '—'}</strong>
          </div>
          <div>
            <span>Notification failures</span>
            <strong>{status?.checkout_notification_consecutive_failures ?? '—'}</strong>
          </div>
          <div>
            <span>Last notification run</span>
            <strong>
              {status?.checkout_notification_last_success_at
                ? new Date(status.checkout_notification_last_success_at).toLocaleString()
                : '—'}
            </strong>
          </div>
          <div>
            <span>Last notifications</span>
            <strong>
              {status
                ? `${status.checkout_notification_last_created} / ${status.checkout_notification_last_examined}`
                : '—'}
            </strong>
          </div>
        </div>
        {status && status.checkout_notification_consecutive_failures > 0 && (
          <p className="maintenance-warning" role="alert">
            Checkout expiry notification generation failed{' '}
            {status.checkout_notification_consecutive_failures} consecutive time(s).
          </p>
        )}
        <section className="maintenance-card" aria-labelledby="worker-health-title">
          <div className="maintenance-card-heading">
            <div>
              <h3 id="worker-health-title">CAD server availability</h3>
              <p>
                Queue workers use heartbeats. Standalone CAD servers use direct availability checks.
                CAD preprocessors are checked through their HTTP services.
              </p>
            </div>
          </div>
          {externalStatusError && (
            <p className="error" role="alert">
              {externalStatusError}
            </p>
          )}
          <div className="worker-health-grid">
              {(status?.workers ?? []).map((worker) => (
                <article className="worker-health-card" key={worker.worker_type}>
                  <div className="maintenance-title-row">
                    <strong>{worker.worker_type}</strong>
                    <span
                      className={`maintenance-health ${worker.availability === 'ONLINE' ? 'healthy' : 'degraded'}`}
                    >
                      {worker.availability}
                    </span>
                    {worker.status && <span className="worker-state">{worker.status}</span>}
                  </div>
                  <dl>
                    <div>
                      <dt>Machine</dt>
                      <dd>{worker.machine_name ?? 'No heartbeat'}</dd>
                    </div>
                    <div>
                      <dt>Endpoint</dt>
                      <dd>{worker.configured_url ?? 'Queue worker'}</dd>
                    </div>
                    <div>
                      <dt>Worker</dt>
                      <dd>{worker.worker_version ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>CAD version</dt>
                      <dd>{worker.application_version ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>Adapter</dt>
                      <dd>{worker.adapter_version ?? '—'}</dd>
                    </div>
                    <div>
                      <dt>Compatibility</dt>
                      <dd>{worker.compatibility_state}</dd>
                    </div>
                    <div>
                      <dt>Capabilities</dt>
                      <dd>
                        {Object.entries(worker.capabilities)
                          .filter(([, value]) => value === true)
                          .map(([name]) => name.replaceAll('_', ' '))
                          .join(', ') || '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>Last seen</dt>
                      <dd>
                        {worker.last_heartbeat_at
                          ? new Date(worker.last_heartbeat_at).toLocaleString()
                          : 'Never'}
                      </dd>
                    </div>
                    <div>
                      <dt>Current work</dt>
                      <dd>{worker.current_job ? 'Processing' : 'None'}</dd>
                    </div>
                  </dl>
                  {worker.detail && <p className="maintenance-warning">{worker.detail}</p>}
                </article>
              ))}
            {(['worker', 'analyzer'] as const).map((system) => {
              const connection = cadWorkerStatus?.[system];
              if (!connection?.url) return null;
              return (
                <article className="worker-health-card" key={system}>
                  <div className="maintenance-title-row">
                    <strong>
                      {String(
                        connection?.capabilities?.workerName ??
                          connection?.capabilities?.cadSystem ??
                          'CAD'
                      )}
                      {system === 'worker' ? ' worker' : ' preprocessor'}
                    </strong>
                    <span
                      className={`maintenance-health ${connection?.availability === 'ONLINE' ? 'healthy' : 'degraded'}`}
                    >
                      {connection?.availability ?? 'CHECKING'}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>Endpoint</dt>
                      <dd>{connection?.url ?? 'Not configured'}</dd>
                    </div>
                    <div>
                      <dt>Worker version</dt>
                      <dd>{String(connection?.capabilities?.workerVersion ?? '—')}</dd>
                    </div>
                    <div>
                      <dt>File formats</dt>
                      <dd>
                        {Array.isArray(connection?.capabilities?.sourceExtensions)
                          ? connection.capabilities.sourceExtensions.join(', ')
                          : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>Configuration</dt>
                      <dd>
                        {connection?.configuration_source?.replaceAll('_', ' ') ?? 'Checking'}
                      </dd>
                    </div>
                    <div>
                      <dt>Last checked</dt>
                      <dd>
                        {connection?.checked_at
                          ? new Date(connection.checked_at).toLocaleString()
                          : 'Waiting'}
                      </dd>
                    </div>
                  </dl>
                  {connection?.detail && <p className="maintenance-warning">{connection.detail}</p>}
                </article>
              );
            })}
            <ExternalWorkerAvailability workers={externalStatuses} />
          </div>
        </section>
        <ExternalWorkers onChanged={refreshExternalStatuses} />
        <section className="maintenance-card" aria-labelledby="cad-servers-title">
          <div className="maintenance-card-heading">
            <div>
              <h3 id="cad-servers-title">CAD server settings</h3>
              <p>
                Configure a default external CAD worker and preprocessor. An enabled matching worker
                registration takes precedence. Without a matching worker, native CAD
                processing is unavailable. An empty preprocessor URL disables server-side analysis.
              </p>
            </div>
          </div>
          <form className="cad-worker-settings-form" onSubmit={onSaveSettings}>
            <label>
              Default CAD worker URL
              <input
                name="cadWorkerUrl"
                type="url"
                placeholder="http://host.docker.internal:8090"
                value={cadWorkerSettings.worker_url ?? ''}
                onChange={(event) => {
                  setCadWorkerSettings((current) => ({
                    ...current,
                    worker_url: event.target.value || null,
                  }));
                  setCadWorkerSettingsSaved(false);
                }}
              />
            </label>
            <label>
              CAD preprocessor URL
              <input
                name="cadAnalyzerUrl"
                type="url"
                placeholder="http://host.docker.internal:8090"
                value={cadWorkerSettings.analyzer_url ?? ''}
                onChange={(event) => {
                  setCadWorkerSettings((current) => ({
                    ...current,
                    analyzer_url: event.target.value || null,
                  }));
                  setCadWorkerSettingsSaved(false);
                }}
              />
            </label>
            <div className="cad-worker-settings-actions">
              <button
                type="submit"
                disabled={cadWorkerSettingsBusy || cadWorkerStatusBusy || loading}
              >
                {cadWorkerSettingsBusy ? 'Saving…' : 'Save CAD server URLs'}
              </button>
              {cadWorkerSettingsSaved && <span role="status">Saved</span>}
              <button
                type="button"
                disabled={cadWorkerSettingsBusy || cadWorkerStatusBusy || loading}
                onClick={() => {
                  void onRefresh();
                  void refreshExternalStatuses();
                }}
              >
                {cadWorkerStatusBusy ? 'Checking…' : 'Check status'}
              </button>
            </div>
          </form>
        </section>
        <section className="maintenance-card" aria-labelledby="checkout-notification-title">
          <div className="maintenance-card-heading">
            <div>
              <div className="maintenance-title-row">
                <h3 id="checkout-notification-title">Checkout expiry notifications</h3>
                <span
                  className={`maintenance-health ${(status?.checkout_notification_health ?? 'HEALTHY').toLowerCase()}`}
                >
                  {status?.checkout_notification_health ?? 'Loading'}
                </span>
              </div>
              <p>
                Generate missing warnings for checkouts expiring within the configured window.
                Existing warnings remain deduplicated and checkout leases are never extended.
              </p>
            </div>
            <button
              type="button"
              disabled={notificationBusy}
              onClick={() => void onRunNotifications()}
            >
              {notificationBusy ? 'Generating…' : 'Generate notifications now'}
            </button>
          </div>
          {notificationRun && (
            <p className="maintenance-result" role="status">
              Examined {notificationRun.examined} eligible checkout(s); created{' '}
              {notificationRun.created} notification(s).
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
