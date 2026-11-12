import type { Dispatch, FormEvent, SetStateAction } from "react";
import type {
  CadLicenseInfo, CadWorkerSettings, CadWorkerStatus, CheckoutNotificationRun,
  NxDevelopmentCapabilities, Status,
} from "./types";

const formatBytes = (bytes: number) => `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;

function isNxDevelopmentCapabilities(value: unknown): value is NxDevelopmentCapabilities {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.enabled === "boolean"
    && Array.isArray(candidate.documentKinds)
    && candidate.documentKinds.every((item) => typeof item === "string")
    && Array.isArray(candidate.capabilities)
    && candidate.capabilities.every((item) => typeof item === "string")
    && typeof candidate.detail === "string";
}

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
  status, cadWorkerStatus, cadWorkerSettings, setCadWorkerSettings,
  setCadWorkerSettingsSaved, cadWorkerSettingsBusy, cadWorkerStatusBusy, cadWorkerSettingsSaved,
  loading, notificationBusy, notificationRun, onSaveSettings, onRefresh,
  onRunNotifications,
}: OperationsPanelProps) {
  return (
          <div className="admin-tab-panel">
            <div className="admin-section" style={{ padding: "1.25rem" }}>
              <h2>Operations</h2>
              <div className="admin-stats">
                <div><span>System</span><strong>{status?.status ?? "—"}</strong></div>
                <div><span>Queue depth</span><strong>{status?.queue_depth ?? "—"}</strong></div>
                <div><span>Active staging</span><strong>{status?.active_staging_sessions ?? "—"}</strong></div>
                <div><span>Stored artifacts</span><strong>{status ? formatBytes(status.verified_artifact_bytes) : "—"}</strong></div>
                <div><span>Storage free</span><strong>{status ? formatBytes(status.storage_free_bytes) : "—"}</strong></div>
                <div><span>Checkout notifications</span><strong>{status?.checkout_notification_health ?? "—"}</strong></div>
                <div><span>Notification failures</span><strong>{status?.checkout_notification_consecutive_failures ?? "—"}</strong></div>
                <div><span>Last notification run</span><strong>{status?.checkout_notification_last_success_at ? new Date(status.checkout_notification_last_success_at).toLocaleString() : "—"}</strong></div>
                <div><span>Last notifications</span><strong>{status ? `${status.checkout_notification_last_created} / ${status.checkout_notification_last_examined}` : "—"}</strong></div>
              </div>
              {status && status.checkout_notification_consecutive_failures > 0 && (
                <p className="maintenance-warning" role="alert">
                  Checkout expiry notification generation failed {status.checkout_notification_consecutive_failures} consecutive time(s).
                </p>
              )}
              <section className="maintenance-card" aria-labelledby="worker-health-title">
                <div className="maintenance-card-heading">
                  <div>
                    <h3 id="worker-health-title">CAD server availability</h3>
                    <p>Queue workers use heartbeats. Standalone CAD servers use direct availability checks. The FreeCAD analyzer check requires a standalone worker.</p>
                  </div>
                </div>
                <div className="worker-health-grid">
                  {(status?.workers ?? [])
                    .filter((worker) => !["AUTOCAD", "INVENTOR"].includes(
                      worker.worker_type.toUpperCase(),
                    ))
                    .map((worker) => (
                    <article className="worker-health-card" key={worker.worker_type}>
                      <div className="maintenance-title-row">
                        <strong>{worker.worker_type.toUpperCase() === "FREECAD" ? "EMBEDDED FREECAD" : worker.worker_type}</strong>
                        <span className={`maintenance-health ${worker.availability === "ONLINE" ? "healthy" : "degraded"}`}>
                          {worker.availability}
                        </span>
                        {worker.status && <span className="worker-state">{worker.status}</span>}
                      </div>
                      <dl>
                        <div><dt>Machine</dt><dd>{worker.machine_name ?? "No heartbeat"}</dd></div>
                        <div><dt>Endpoint</dt><dd>{worker.configured_url ?? "Queue worker"}</dd></div>
                        <div><dt>Worker</dt><dd>{worker.worker_version ?? "—"}</dd></div>
                        <div><dt>CAD version</dt><dd>{worker.application_version ?? "—"}</dd></div>
                        <div><dt>Adapter</dt><dd>{worker.adapter_version ?? "—"}</dd></div>
                        <div><dt>Compatibility</dt><dd>{worker.compatibility_state}</dd></div>
                        <div><dt>Capabilities</dt><dd>{Object.entries(worker.capabilities)
                          .filter(([, value]) => value === true)
                          .map(([name]) => name.replaceAll("_", " "))
                          .join(", ") || "—"}</dd></div>
                        <div><dt>Last seen</dt><dd>{worker.last_heartbeat_at ? new Date(worker.last_heartbeat_at).toLocaleString() : "Never"}</dd></div>
                        <div><dt>Current work</dt><dd>{worker.current_job ? "Processing" : "None"}</dd></div>
                      </dl>
                      {worker.detail && <p className="maintenance-warning">{worker.detail}</p>}
                      {worker.worker_type.toUpperCase() === "FREECAD" && cadWorkerSettings.freecad_url && (
                        <p>Standalone FreeCAD is configured for conversion. This card reports the separate embedded worker.</p>
                      )}
                    </article>
                  ))}
                  {(["freecad", "freecad_analyzer"] as const).map((system) => {
                    const connection = cadWorkerStatus?.[system];
                    return (
                      <article className="worker-health-card" key={system}>
                        <div className="maintenance-title-row">
                          <strong>{system === "freecad" ? "STANDALONE FREECAD" : "FREECAD ANALYZER"}</strong>
                          <span className={`maintenance-health ${connection?.availability === "ONLINE" ? "healthy" : "degraded"}`}>
                            {connection?.availability ?? "CHECKING"}
                          </span>
                        </div>
                        <dl>
                          <div><dt>Endpoint</dt><dd>{connection?.url ?? "Not configured"}</dd></div>
                          <div><dt>Configuration</dt><dd>{connection?.configuration_source?.replaceAll("_", " ") ?? "Checking"}</dd></div>
                          <div><dt>Last checked</dt><dd>{connection?.checked_at ? new Date(connection.checked_at).toLocaleString() : "Waiting"}</dd></div>
                        </dl>
                        {connection?.detail && <p className="maintenance-warning">{connection.detail}</p>}
                      </article>
                    );
                  })}
                  {(["autocad", "inventor", "nx"] as const).map((system) => {
                    const connection = cadWorkerStatus?.[system];
                    const license = connection?.capabilities.license as CadLicenseInfo | undefined;
                    const developmentValue = connection?.capabilities.development;
                    const nxDevelopment = isNxDevelopmentCapabilities(developmentValue)
                      ? developmentValue
                      : undefined;
                    const label = system === "autocad" ? "AUTOCAD" : system === "inventor" ? "INVENTOR" : "SIEMENS NX";
                    const readiness = connection?.processing_readiness;
                    const configurationSource = connection?.configuration_source;
                    const configurationLabel = configurationSource === "ADMIN_OVERRIDE"
                      ? "Administrator override"
                      : configurationSource === "ENVIRONMENT_DEFAULT"
                        ? "Deployment default"
                        : configurationSource === "ADMIN_DISABLED"
                          ? "Disabled by administrator"
                          : "Not configured";
                    const readinessLabel = connection?.availability === "NOT_CONFIGURED"
                      ? "Not configured"
                      : connection?.availability !== "ONLINE"
                        ? "Endpoint unavailable"
                        : readiness === "READY"
                          ? "Worker reports ready — routing remains disabled"
                          : readiness === "NOT_READY"
                            ? "Not ready — production routing disabled"
                            : readiness === "UNKNOWN"
                              ? "Could not confirm readiness"
                              : "Not applicable";
                    return (
                      <article className="worker-health-card" key={system}>
                        <div className="maintenance-title-row">
                          <strong>{label}</strong>
                          <span className={`maintenance-health ${connection?.availability === "ONLINE" ? "healthy" : "degraded"}`}>
                            {connection?.availability ?? "CHECKING"}
                          </span>
                        </div>
                        <dl>
                          <div><dt>Endpoint</dt><dd>{connection?.url ?? "Not configured"}</dd></div>
                          <div><dt>Configuration</dt><dd>{configurationLabel}</dd></div>
                          <div>
                            <dt>License</dt>
                            <dd>{license?.status ?? (connection?.availability === "ONLINE" ? "UNVERIFIED" : "—")}</dd>
                          </div>
                          {license?.lastVerifiedAt && (
                            <div><dt>License verified</dt><dd>{new Date(license.lastVerifiedAt).toLocaleString()}</dd></div>
                          )}
                          {system === "nx" && (
                            <div><dt>Production gate</dt><dd>{readiness ? readinessLabel : "Checking"}</dd></div>
                          )}
                          {system === "nx" && connection?.readiness_code && (
                            <div><dt>Gate code</dt><dd>{connection.readiness_code}</dd></div>
                          )}
                          {system === "nx" && nxDevelopment?.enabled && (
                            <>
                              <div><dt>Development scope</dt><dd>{nxDevelopment.documentKinds.join(", ") || "—"}</dd></div>
                              <div><dt>Development features</dt><dd>{nxDevelopment.capabilities.join(", ") || "—"}</dd></div>
                            </>
                          )}
                          <div><dt>Last checked</dt><dd>{connection?.checked_at ? new Date(connection.checked_at).toLocaleString() : "Waiting"}</dd></div>
                        </dl>
                        {license?.detail && <p className="worker-license-detail">{license.detail}</p>}
                        {system === "nx" && nxDevelopment?.enabled && (
                          <p className="maintenance-warning">{nxDevelopment.detail}</p>
                        )}
                        {system === "nx" && connection?.readiness_detail && (
                          <p className="maintenance-warning">{connection.readiness_detail}</p>
                        )}
                        {connection?.detail && <p className="maintenance-warning">{connection.detail}</p>}
                      </article>
                    );
                  })}
                </div>
              </section>
              <section className="maintenance-card" aria-labelledby="licensed-cad-servers-title">
                <div className="maintenance-card-heading">
                  <div>
                    <h3 id="licensed-cad-servers-title">CAD server settings</h3>
                    <p>Configure standalone CAD server URLs. Leave a FreeCAD worker URL empty to use the embedded worker. Leave an analyzer URL empty to disable server-side analysis. Siemens NX remains unavailable for production routing until its worker is qualified.</p>
                  </div>
                </div>
                <form className="cad-worker-settings-form" onSubmit={onSaveSettings}>
                  <label>
                    Standalone FreeCAD worker URL
                    <input name="freecadUrl" type="url" placeholder="http://host.docker.internal:8090"
                      value={cadWorkerSettings.freecad_url ?? ""}
                      onChange={(event) => {
                        setCadWorkerSettings((current) => ({ ...current, freecad_url: event.target.value || null }));
                        setCadWorkerSettingsSaved(false);
                      }} />
                  </label>
                  <label>
                    FreeCAD analyzer URL
                    <input name="freecadAnalyzerUrl" type="url" placeholder="http://host.docker.internal:8090"
                      value={cadWorkerSettings.freecad_analyzer_url ?? ""}
                      onChange={(event) => {
                        setCadWorkerSettings((current) => ({ ...current, freecad_analyzer_url: event.target.value || null }));
                        setCadWorkerSettingsSaved(false);
                      }} />
                  </label>
                  <label>
                    AutoCAD server URL
                    <input
                      name="autocadUrl"
                      type="url"
                      placeholder="https://autocad.internal.example:5080"
                      value={cadWorkerSettings.autocad_url ?? ""}
                      onChange={(event) => {
                        setCadWorkerSettings((current) => ({ ...current, autocad_url: event.target.value || null }));
                        setCadWorkerSettingsSaved(false);
                      }}
                    />
                  </label>
                  <label>
                    Inventor server URL
                    <input
                      name="inventorUrl"
                      type="url"
                      placeholder="https://inventor.internal.example:5091"
                      value={cadWorkerSettings.inventor_url ?? ""}
                      onChange={(event) => {
                        setCadWorkerSettings((current) => ({ ...current, inventor_url: event.target.value || null }));
                        setCadWorkerSettingsSaved(false);
                      }}
                    />
                  </label>
                  <label>
                    Siemens NX server URL
                    <input
                      name="nxUrl"
                      type="url"
                      placeholder="https://nx.internal.example:5092"
                      value={cadWorkerSettings.nx_url ?? ""}
                      onChange={(event) => {
                        setCadWorkerSettings((current) => ({ ...current, nx_url: event.target.value || null }));
                        setCadWorkerSettingsSaved(false);
                      }}
                    />
                  </label>
                  <div className="cad-worker-settings-actions">
                    <button type="submit" disabled={cadWorkerSettingsBusy || cadWorkerStatusBusy || loading}>
                      {cadWorkerSettingsBusy ? "Saving…" : "Save CAD server URLs"}
                    </button>
                    {cadWorkerSettingsSaved && <span role="status">Saved</span>}
                    <button type="button" disabled={cadWorkerSettingsBusy || cadWorkerStatusBusy || loading} onClick={() => void onRefresh()}>
                      {cadWorkerStatusBusy ? "Checking…" : "Check status"}
                    </button>
                  </div>
                </form>
              </section>
              <section className="maintenance-card" aria-labelledby="checkout-notification-title">
                <div className="maintenance-card-heading">
                  <div>
                    <div className="maintenance-title-row">
                      <h3 id="checkout-notification-title">Checkout expiry notifications</h3>
                      <span className={`maintenance-health ${(status?.checkout_notification_health ?? "HEALTHY").toLowerCase()}`}>
                        {status?.checkout_notification_health ?? "Loading"}
                      </span>
                    </div>
                    <p>
                      Generate missing warnings for checkouts expiring within the configured window.
                      Existing warnings remain deduplicated and checkout leases are never extended.
                    </p>
                  </div>
                  <button type="button" disabled={notificationBusy}
                    onClick={() => void onRunNotifications()}>
                    {notificationBusy ? "Generating…" : "Generate notifications now"}
                  </button>
                </div>
                {notificationRun && <p className="maintenance-result" role="status">
                  Examined {notificationRun.examined} eligible checkout(s); created {notificationRun.created} notification(s).
                </p>}
              </section>
            </div>
          </div>
  );
}

