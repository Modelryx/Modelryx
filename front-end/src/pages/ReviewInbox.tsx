import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ReviewInboxEntry } from "../workflow-api";
import { ActionDialog } from "../components/ActionDialog";

type DelegateCandidate = { id: string; display_name: string; role: string };
type Delegation = {
  id: string; grantor_id: string; delegate_id: string; starts_at: string;
  ends_at: string; is_active: boolean; can_revoke: boolean;
};

export function ReviewInbox({ canReview, embedded = false, selectedRevisionId, onChanged }: { canReview: boolean; embedded?: boolean; selectedRevisionId?: string; onChanged?: () => void }) {
  const [searchParams] = useSearchParams();
  const revisionId = selectedRevisionId ?? searchParams.get("revisionId");
  const [entries, setEntries] = useState<ReviewInboxEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [deciding, setDeciding] = useState<string | null>(null);
  const [decisionDraft, setDecisionDraft] = useState<{ entry: ReviewInboxEntry; decision: "APPROVED" | "REJECTED" } | null>(null);
  const [decisionComment, setDecisionComment] = useState("");
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [delegating, setDelegating] = useState(false);
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [candidates, setCandidates] = useState<DelegateCandidate[]>([]);
  const visibleEntries = revisionId ? entries.filter(entry => entry.approval.revision_id === revisionId) : entries;

  const load = useCallback(() => {
    setLoading(true);
    setLoadError("");
    Promise.all([
      api<ReviewInboxEntry[]>("/approval-requests/inbox"),
      embedded ? Promise.resolve([] as Delegation[]) : api<Delegation[]>("/approval-delegations"),
      embedded ? Promise.resolve([] as DelegateCandidate[]) : api<DelegateCandidate[]>("/approval-delegations/candidates"),
    ])
      .then(([loadedEntries, loadedDelegations, loadedCandidates]) => {
        setEntries(loadedEntries);
        setDelegations(loadedDelegations);
        setCandidates(loadedCandidates);
      })
      .catch((reason) => setLoadError(reason instanceof Error ? reason.message : "Unable to load reviews"))
      .finally(() => setLoading(false));
  }, [embedded]);

  useEffect(() => { if (canReview) load(); }, [load, canReview]);

  const decide = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!decisionDraft || !decisionComment.trim() || deciding) return;
    const { entry, decision } = decisionDraft;
    if (decision === "APPROVED" && entry.revision_state === "READY_WITH_WARNINGS" && !warningsAcknowledged) return;
    setDeciding(entry.approval.id);
    setActionError("");
    try {
      await api(
        `/approval-requests/${entry.approval.id}/decision`,
        "POST",
        { decision, comment: decisionComment.trim(), warnings_acknowledged: warningsAcknowledged || decision === "REJECTED" },
      );
      setDecisionDraft(null);
      load();
      onChanged?.();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Unable to record decision");
    } finally { setDeciding(null); }
  };

  const createDelegation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const endsAt = new Date(String(data.get("endsAt")));
    if (Number.isNaN(endsAt.valueOf()) || delegating) return;
    const candidate = candidates.find(value => value.id === String(data.get("delegateId")));
    if (!window.confirm(`Delegate reviews to ${candidate?.display_name ?? "the selected reviewer"} until ${endsAt.toLocaleString()}?`)) return;
    setActionError("");
    setDelegating(true);
    try {
      await api("/approval-delegations", "POST", {
        delegate_id: String(data.get("delegateId")),
        ends_at: endsAt.toISOString(),
      });
      form.reset();
      load();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Unable to create delegation");
    } finally { setDelegating(false); }
  };

  const revokeDelegation = async (delegation: Delegation) => {
    if (delegating || !window.confirm("Revoke this review delegation?")) return;
    setActionError("");
    setDelegating(true);
    try {
      await api(`/approval-delegations/${delegation.id}`, "DELETE");
      load();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Unable to revoke delegation");
    } finally { setDelegating(false); }
  };

  if (!canReview) {
    return <section className="page"><h1>Reviews</h1><p className="error">Reviewer role required.</p></section>;
  }

  return (
    <section className={`page review-inbox-page${embedded ? " review-inbox-page--embedded" : ""}`}>
      <div className="workspace-header">
        <div>
          {embedded ? <h2>Review</h2> : <h1>Review inbox</h1>}
          <p className="page-subtitle">{revisionId ? "Reviews for the selected revision." : "Oldest requests appear first."} Decisions apply only to the listed snapshot. {revisionId && <Link to="/reviews">Show all reviews</Link>}</p>
        </div>
        <button type="button" onClick={load} disabled={loading}>Refresh</button>
      </div>
      {actionError && !decisionDraft && <p className="error" role="alert">{actionError}</p>}
      {!embedded && !loadError && <section className="item-revision-panel">
        <h2>Absence delegation</h2>
        <form className="review-inbox-actions" onSubmit={createDelegation}>
          <select name="delegateId" aria-label="Delegate reviewer" required defaultValue="">
            <option value="" disabled>Select delegate</option>
            {candidates.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.display_name} ({candidate.role})
              </option>
            ))}
          </select>
          <input name="endsAt" type="datetime-local" required aria-label="Delegation end time" />
          <button type="submit" disabled={delegating}>Delegate reviews</button>
        </form>
        {delegations.filter((value) => value.is_active).map((delegation) => (
          <p className="muted" key={delegation.id}>
            Delegated to {candidates.find((value) => value.id === delegation.delegate_id)?.display_name ?? delegation.delegate_id}
            {" until "}{new Date(delegation.ends_at).toLocaleString()}
            {delegation.can_revoke && <button type="button" disabled={delegating} onClick={() => void revokeDelegation(delegation)}>Revoke</button>}
          </p>
        ))}
      </section>}
      {loading ? <p role="status">Loading review requests…</p> : loadError ? (
        <div className="section-error" role="alert"><span>{loadError}</span><button type="button" onClick={load}>Retry</button></div>
      ) : visibleEntries.length === 0 ? (
        <div className="empty-state"><h2>No pending reviews{revisionId ? " for this revision" : ""}</h2><p>New revision approval requests will appear here.</p></div>
      ) : (
        <div className="review-inbox-list">
          {visibleEntries.map((entry) => (
            <article className="review-inbox-card" key={entry.approval.id}>
              <div>
                <div className="review-inbox-title">
                  <Link to={`/items/${entry.item_id}/revisions/${entry.approval.revision_id}`}>
                    {entry.item_number} · Revision {entry.revision_code}
                  </Link>
                  <span className={`status-badge ${entry.approval.status.toLowerCase()}`}>
                    {entry.approval.status}
                  </span>
                </div>
                <strong>{entry.item_name}</strong>
                <p className="muted">Snapshot {entry.snapshot_sequence} · Processing {entry.revision_state.replaceAll("_", " ")} · Approvals {entry.approval.approval_count}/{entry.approval.required_approvals} · Due {new Date(entry.approval.due_at).toLocaleString()}</p>
                {entry.approval.request_comment && <p>{entry.approval.request_comment}</p>}
              </div>
              <div className="review-inbox-actions">
                <Link className="button" to={`/items/${entry.item_id}/revisions/${entry.approval.revision_id}`}>Open revision</Link>
                <Link className="button" to={`/items/${encodeURIComponent(entry.item_id)}/revisions/${encodeURIComponent(entry.approval.revision_id)}?section=compare&to=${encodeURIComponent(entry.approval.revision_id)}`}>Compare changes</Link>
                <button type="button" className="primary" disabled={!entry.approval.can_decide || deciding !== null || entry.approval.status === "STALE"} onClick={() => { setDecisionDraft({ entry, decision: "APPROVED" }); setDecisionComment(""); setWarningsAcknowledged(false); setActionError(""); }}>Approve</button>
                <button type="button" disabled={!entry.approval.can_decide || deciding !== null || entry.approval.status === "STALE"} onClick={() => { setDecisionDraft({ entry, decision: "REJECTED" }); setDecisionComment(""); setWarningsAcknowledged(false); setActionError(""); }}>Reject</button>
              </div>
            </article>
          ))}
        </div>
      )}
      {decisionDraft && <ActionDialog title={`${decisionDraft.decision === "APPROVED" ? "Approve" : "Reject"} revision`} onClose={() => { if (!deciding) setDecisionDraft(null); }}>
        <form onSubmit={(event) => void decide(event)}>
          <p><strong>{decisionDraft.entry.item_number}</strong> · {decisionDraft.entry.item_name} · Revision {decisionDraft.entry.revision_code}</p>
          <p>Decision applies to content snapshot {decisionDraft.entry.snapshot_sequence}. {decisionDraft.decision === "APPROVED" ? "Approval records your review of this exact version." : "The requester will need to address your feedback."}</p>
          <label className="dialog-field">{decisionDraft.decision === "APPROVED" ? "Approval evidence" : "Required rework"}<textarea autoFocus required rows={4} value={decisionComment} onChange={event => setDecisionComment(event.target.value)} /></label>
          {decisionDraft.decision === "APPROVED" && decisionDraft.entry.revision_state === "READY_WITH_WARNINGS" && <label className="dialog-check"><input type="checkbox" required checked={warningsAcknowledged} onChange={event => setWarningsAcknowledged(event.target.checked)} /> I reviewed and accept the processing warnings.</label>}
          {actionError && <p className="error" role="alert">{actionError}</p>}
          <div className="dialog-actions"><button type="button" onClick={() => setDecisionDraft(null)} disabled={!!deciding}>Cancel</button><button type="submit" className={decisionDraft.decision === "APPROVED" ? "primary" : ""} disabled={!!deciding || !decisionComment.trim() || (decisionDraft.decision === "APPROVED" && decisionDraft.entry.revision_state === "READY_WITH_WARNINGS" && !warningsAcknowledged)}>{deciding ? "Saving…" : decisionDraft.decision === "APPROVED" ? "Record approval" : "Record rejection"}</button></div>
        </form>
      </ActionDialog>}
    </section>
  );
}
