import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../workflow-api";
import { randomUUID } from "../../uuid";
import { dateTime, displayMetadataValue, formatPropertyName } from "./formatters";
import type {
  CADExecution, CADInspection, CADPlan, CADSession, CADWorkerSummary,
  ContentSnapshot, ProcessingJob,
} from "./types";
import "./cadSessions.css";

export function ProcessingSection({
  jobs,
  onChanged,
}: {
  jobs: ProcessingJob[];
  onChanged: () => Promise<void>;
}) {
  const [busyJobId, setBusyJobId] = useState("");
  const [message, setMessage] = useState("");

  const runAction = async (job: ProcessingJob, action: "retry" | "cancel") => {
    if (action === "cancel" && !window.confirm("Cancel this pending processing job?")) return;
    setBusyJobId(job.id);
    setMessage("");
    try {
      await api(`/processing-jobs/${job.id}/${action}`, "POST");
      setMessage(action === "retry" ? "Retry queued." : "Pending job cancelled.");
      await onChanged();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : `Unable to ${action} processing`);
    } finally {
      setBusyJobId("");
    }
  };

  return (
    <div className="item-revision-section">
      <div className="item-revision-section-header">
        <div>
          <h2>Processing</h2>
          <p className="muted">Persistent conversion, metadata, and preview job history for this revision.</p>
        </div>
      </div>
      {message && <p role="status" className="muted">{message}</p>}
      <section className="item-revision-panel processing-jobs-panel">
        {jobs.length ? (
          <div className="processing-job-list">
            {jobs.map((job) => (
              <article className="processing-job-card" key={job.id}>
                <div className="processing-job-heading">
                  <div>
                    <strong>{formatPropertyName(job.type)}</strong>
                    <span className={`status-badge processing-${job.state.toLowerCase()}`}>{formatPropertyName(job.state)}</span>
                  </div>
                  <span>{job.progress_percent}%</span>
                </div>
                <progress max="100" value={job.progress_percent} aria-label={`${job.type} progress`} />
                <dl className="processing-job-details">
                  <div><dt>Stage</dt><dd>{formatPropertyName(job.stage)}</dd></div>
                  <div><dt>Attempt</dt><dd>{job.attempt_count}</dd></div>
                  <div><dt>Queued</dt><dd>{dateTime(job.available_at)}</dd></div>
                  <div><dt>Started</dt><dd>{dateTime(job.started_at)}</dd></div>
                  <div><dt>Finished</dt><dd>{dateTime(job.finished_at)}</dd></div>
                </dl>
                {job.error_detail && <p className="processing-job-error">{job.error_detail}</p>}
                {job.can_control && (
                  <div className="processing-job-actions">
                    {job.state === "FAILED" && job.stage !== "CANCELLED" && (
                      <button type="button" disabled={busyJobId === job.id} onClick={() => void runAction(job, "retry")}>Retry</button>
                    )}
                    {job.state === "PENDING" && (
                      <button type="button" disabled={busyJobId === job.id} onClick={() => void runAction(job, "cancel")}>Cancel</button>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
        ) : <p className="muted">No processing jobs exist for this revision.</p>}
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
            Immutable Artifact manifests for this revision. Restoring a historical snapshot opens
            a staging session for review and creates a new snapshot on Submit.
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
                      <td><strong>Snapshot {snapshot.sequence}</strong></td>
                      <td>{dateTime(snapshot.created_at)}</td>
                      <td>{snapshot.artifact_count}</td>
                      <td className="mono version-hash" title={snapshot.commit_id}>{snapshot.commit_id}</td>
                      <td>{isCurrent ? <span className="current-version-badge">Current</span> : "Historical"}</td>
                      <td>
                        {canRestore && !isCurrent && (
                          <Link className="secondary-button" to={
                            `/checkin?mode=edit&itemId=${encodeURIComponent(itemId)}`
                            + `&revisionId=${encodeURIComponent(revisionId)}`
                            + `&sourceSnapshotId=${encodeURIComponent(snapshot.id)}`
                          }>
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
          <div className="empty-state"><strong>No snapshots</strong><p className="muted">This revision has no committed Artifact snapshots.</p></div>
        )}
      </section>
    </div>
  );
}


export function CADSessionsSection({
  itemId,
  revisionId,
  cadSystem,
  sessions,
  onChanged,
  canEdit,
}: {
  itemId: string;
  revisionId: string;
  cadSystem: string;
  canEdit: boolean;
  sessions: CADSession[];
  onChanged: () => Promise<void>;
}) {
  const [inspection, setInspection] = useState<CADInspection>();
  const [workers, setWorkers] = useState<CADWorkerSummary[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState("");
  const [selectedParameter, setSelectedParameter] = useState("");
  const [newValue, setNewValue] = useState("");
  const [material, setMaterial] = useState("");
  const [plan, setPlan] = useState<CADPlan>();
  const [execution, setExecution] = useState<CADExecution>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [inspectionLoading, setInspectionLoading] = useState(true);
  const [workersLoading, setWorkersLoading] = useState(true);
  const [inspectionError, setInspectionError] = useState("");
  const [workersError, setWorkersError] = useState("");
  const [refreshAttempt, setRefreshAttempt] = useState(0);
  const active = sessions.find((session) =>
    ["OPENING", "READY", "EXECUTING", "READY_TO_REVIEW"].includes(session.status),
  );
  const visibleSessions = sessions.filter((session) =>
    !["DISCARDED", "CANCELLED"].includes(session.status),
  );
  const editableParameters = useMemo(() => inspection?.features.flatMap((feature) =>
    feature.parameters.filter((parameter) => parameter.editable && typeof parameter.value === "number")
      .map((parameter) => ({ feature, parameter, key: `${feature.id}\u0000${parameter.name}` })),
  ) ?? [], [inspection]);
  const selected = editableParameters.find((entry) => entry.key === selectedParameter);
  const compatibleWorkers = useMemo(() => workers.filter((worker) =>
    worker.available
    && worker.mutation_enabled
    && worker.cad_system.toLocaleLowerCase() === cadSystem.toLocaleLowerCase()
  ), [workers, cadSystem]);
  const diagnostics = useMemo(() => {
    const counts = new Map<string, number>();
    for (const raw of inspection?.warnings ?? []) {
      const warning = raw.trim();
      if (warning) counts.set(warning, (counts.get(warning) ?? 0) + 1);
    }
    return [...counts].map(([text, count]) => ({ text, count }));
  }, [inspection?.warnings]);
  const readiness = inspectionLoading || workersLoading ? "checking"
    : inspectionError || workersError || !inspection?.mutation_ready || !compatibleWorkers.length ? "unavailable" : "ready";

  useEffect(() => {
    let activeRequest = true;
    setInspectionLoading(true); setInspectionError("");
    api<CADInspection>(`/item-revisions/${revisionId}/cad-inspection`)
      .then((value) => {
        if (!activeRequest) return;
        setInspection(value);
        setMaterial(value.material ?? "");
      })
      .catch((reason) => { if (activeRequest) setInspectionError(reason instanceof Error ? reason.message : "CAD inspection failed"); })
      .finally(() => { if (activeRequest) setInspectionLoading(false); });
    return () => { activeRequest = false; };
  }, [revisionId, refreshAttempt]);

  useEffect(() => {
    let activeRequest = true;
    setWorkersLoading(true); setWorkersError("");
    api<CADWorkerSummary[]>("/cad/workers")
      .then((values) => { if (activeRequest) setWorkers(values); })
      .catch((reason) => { if (activeRequest) setWorkersError(reason instanceof Error ? reason.message : "Unable to discover CAD workers"); })
      .finally(() => { if (activeRequest) setWorkersLoading(false); });
    return () => { activeRequest = false; };
  }, [revisionId, refreshAttempt]);

  useEffect(() => {
    if (!compatibleWorkers.some((worker) => worker.worker_id === selectedWorkerId)) {
      setSelectedWorkerId(compatibleWorkers[0]?.worker_id ?? "");
    }
  }, [compatibleWorkers, selectedWorkerId]);

  useEffect(() => {
    if (!execution || !["QUEUED", "RUNNING"].includes(execution.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const updated = await api<CADExecution>(`/cad-executions/${execution.id}`);
        setExecution(updated);
        if (updated.status === "SUCCEEDED") {
          setMessage("CAD operation completed and is ready for review.");
          await onChanged();
        } else if (updated.status === "FAILED") {
          setError(String(updated.error.message ?? "CAD execution failed"));
          await onChanged();
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Unable to refresh CAD execution");
      }
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [execution, onChanged]);

  const createSession = async () => {
    if (!selectedWorkerId) {
      setError(`No compatible ${cadSystem || "CAD"} editing worker is online.`);
      return;
    }
    setBusy(true); setError(""); setMessage(""); setPlan(undefined); setExecution(undefined);
    try {
      await api(`/item-revisions/${revisionId}/cad-sessions`, "POST", {
        worker_id: selectedWorkerId,
      });
      setMessage("Controlled CAD session created.");
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create CAD session"); }
    finally { setBusy(false); }
  };

  const deleteSession = async (session: CADSession) => {
    if (!confirm("Delete this CAD session and its isolated working copy? The audit record will be retained.")) return;
    setBusy(true); setError("");
    try {
      await api(`/cad-sessions/${session.id}/discard`, "POST", {});
      setPlan(undefined); setExecution(undefined); setMessage("CAD session deleted.");
      await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to delete CAD session"); }
    finally { setBusy(false); }
  };

  const proposeParameter = async () => {
    if (!active || !selected || !Number.isFinite(Number(newValue))) return;
    setBusy(true); setError(""); setMessage("");
    try {
      setPlan(await api<CADPlan>(`/cad-sessions/${active.id}/plans`, "POST", {
        target_id: selected.feature.id,
        parameter: selected.parameter.name,
        value: Number(newValue),
        unit: selected.parameter.unit === "mm" || selected.parameter.unit === "deg"
          ? selected.parameter.unit : null,
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to propose CAD change"); }
    finally { setBusy(false); }
  };

  const proposeMaterial = async () => {
    if (!active || !material.trim()) return;
    setBusy(true); setError(""); setMessage("");
    try {
      setPlan(await api<CADPlan>(`/cad-sessions/${active.id}/plans`, "POST", { material: material.trim() }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to propose material change"); }
    finally { setBusy(false); }
  };

  const confirmPlan = async () => {
    if (!plan || !confirm(`Execute this controlled CAD change?\n\n${String(plan.preview.summary ?? "")}`)) return;
    setBusy(true); setError("");
    try {
      const value = await api<CADExecution>(`/cad-plans/${plan.id}/confirm`, "POST", {
        confirmed: true, idempotency_key: `cad-ui-${randomUUID()}`,
      });
      setExecution(value); setMessage("CAD operation queued."); await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to execute CAD plan"); }
    finally { setBusy(false); }
  };

  return (
    <div className="item-revision-section cad-sessions-section">
      <div className="item-revision-section-header">
        <div>
          <h2>CAD Sessions</h2>
          <p className="muted">Review CAD results or start a controlled edit.</p>
        </div>
        {canEdit && !active && <div className="cad-session-create-actions">
          {compatibleWorkers.length > 1 && <select
            aria-label="CAD editing worker"
            value={selectedWorkerId}
            onChange={(event) => setSelectedWorkerId(event.target.value)}
          >
            {compatibleWorkers.map((worker) => <option key={worker.worker_id} value={worker.worker_id}>
              {worker.worker_id} · {worker.worker_version}
            </option>)}
          </select>}
          <button
            type="button"
            disabled={busy || inspectionLoading || workersLoading || !!inspectionError || !!workersError || !inspection?.mutation_ready || !selectedWorkerId}
            title={!inspection?.mutation_ready
              ? "A verified native CAD artifact is required"
              : !selectedWorkerId ? `No compatible ${cadSystem || "CAD"} editing worker is online` : undefined}
            onClick={() => void createSession()}
          >
            {busy ? "Creating…" : "New CAD session"}
          </button>
        </div>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="version-restore-message" role="status">{message}</p>}
      {canEdit && !active && <div className="cad-session-readiness" data-state={readiness} role="status">
        {inspectionLoading || workersLoading ? <span>Checking CAD edit availability…</span>
          : inspectionError || workersError ? <><strong>Could not check CAD edit availability.</strong><button type="button" onClick={() => setRefreshAttempt(value => value + 1)}>Retry</button></>
          : !inspection?.mutation_ready ? <><strong>CAD editing unavailable</strong><span>A verified native CAD file is required to create a session.</span></>
          : !compatibleWorkers.length ? <><strong>CAD editing unavailable</strong><span>No compatible {cadSystem || "CAD"} editing worker is online. Existing results remain available below.</span><button type="button" onClick={() => setRefreshAttempt(value => value + 1)}>Check again</button></>
          : <><strong>Ready to edit</strong><span>{compatibleWorkers.length} compatible worker{compatibleWorkers.length === 1 ? "" : "s"} online.</span></>}
      </div>}
      {(inspectionError || workersError) && <details className="cad-session-diagnostics"><summary>Availability details</summary><ul>{inspectionError && <li>{inspectionError}</li>}{workersError && <li>{workersError}</li>}</ul></details>}
      {!inspectionLoading && !inspectionError && diagnostics.length > 0 && <details className="cad-session-diagnostics"><summary>CAD inspection details ({diagnostics.length} distinct notice{diagnostics.length === 1 ? "" : "s"})</summary><ul>{diagnostics.map(({ text, count }) => <li key={text}>{text}{count > 1 && <span className="cad-warning-count"> × {count}</span>}</li>)}</ul></details>}
      <section className="cad-session-list" aria-label="CAD sessions">
        <div className="cad-session-list-heading"><h3>Sessions</h3><span>{visibleSessions.length}</span></div>
        {visibleSessions.length ? <div className="cad-session-rows">{visibleSessions.map(session => <article className="cad-session-row" key={session.id}>
          <div><strong className={`cad-session-state cad-session-state--${session.status.toLowerCase()}`}>{session.status.replaceAll("_", " ").toLowerCase()}</strong><p>{dateTime(session.created_at)} · {session.worker_id}</p>{session.status === "FAILED" && typeof session.error?.message === "string" && <p className="cad-session-row-error">{session.error.message}</p>}</div>
          <div className="cad-session-row-actions">
            {session.status === "READY_TO_REVIEW" && <Link className="button button--primary button-link" to={`/items/${itemId}/revisions/${revisionId}/cad-sessions/${session.id}`}>Review result</Link>}
            {canEdit && ["READY", "READY_TO_REVIEW", "FAILED", "EXPIRED"].includes(session.status) && (session.id !== active?.id || session.status === "READY_TO_REVIEW") && <button type="button" className="button button--secondary" disabled={busy} onClick={() => void deleteSession(session)}>Delete</button>}
          </div>
        </article>)}</div> : <p className="cad-session-empty">No CAD sessions yet. {canEdit ? "Create one when a compatible editing worker is available." : "Results will appear here when available."}</p>}
      </section>
      {active?.status === "READY" && canEdit && (
        <section className="item-revision-panel cad-operation-panel">
          <div className="cad-session-title"><div><h3>Controlled change</h3><p className="muted">{inspection?.document_label ?? `${cadSystem || "CAD"} document`} · expires {dateTime(active.expires_at)}</p></div><button type="button" disabled={busy} onClick={() => void deleteSession(active)}>Delete session</button></div>
          <div className="cad-operation-grid">
            <div><label htmlFor="cad-parameter">Editable parameter</label><select id="cad-parameter" value={selectedParameter} onChange={(event) => { setSelectedParameter(event.target.value); setNewValue(""); setPlan(undefined); }}><option value="">Select a parameter</option>{editableParameters.map((entry) => <option key={entry.key} value={entry.key}>{entry.feature.name ?? entry.feature.id} · {formatPropertyName(entry.parameter.name)} ({entry.parameter.value}{entry.parameter.unit ? ` ${entry.parameter.unit}` : ""})</option>)}</select><label htmlFor="cad-new-value">New value</label><input id="cad-new-value" type="number" value={newValue} onChange={(event) => setNewValue(event.target.value)} /><button type="button" disabled={busy || !selected || newValue === ""} onClick={() => void proposeParameter()}>Review parameter change</button></div>
            <div><label htmlFor="cad-material">Material</label><input id="cad-material" value={material} onChange={(event) => { setMaterial(event.target.value); setPlan(undefined); }} placeholder="Aluminum 6061-T6" /><button type="button" disabled={busy || !material.trim() || material.trim() === inspection?.material} onClick={() => void proposeMaterial()}>Review material change</button></div>
          </div>
          {plan && <div className="cad-plan-review"><h3>Plan review</h3><dl className="revision-properties"><div><dt>Operation</dt><dd>{plan.operation}</dd></div><div><dt>Target</dt><dd>{plan.target_id}.{plan.parameter}</dd></div><div><dt>Before</dt><dd>{displayMetadataValue(plan.expected_value)}</dd></div><div><dt>After</dt><dd>{displayMetadataValue(plan.new_value)}</dd></div></dl><button type="button" disabled={busy} onClick={() => void confirmPlan()}>Confirm and execute</button></div>}
        </section>
      )}
      {active?.status === "EXECUTING" && <section className="item-revision-panel"><h3>Executing CAD operation</h3><p className="muted">The controlled worker is processing the isolated copy. This page updates automatically.</p></section>}
    </div>
  );
}

