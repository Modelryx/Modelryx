import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Item, type Revision } from "../api";
import { Button } from "../components/Button";

export function ItemActions({ item, revisions, refresh }: { item: Item; revisions: Revision[]; refresh: () => void }) {
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (busy) return;
    const data = new FormData(event.currentTarget); setBusy(true); setError("");
    try {
      await api(`/items/${encodeURIComponent(item.id)}`, "PATCH", { name: String(data.get("name")).trim(), description: String(data.get("description")).trim() || null, category: String(data.get("category")).trim() || null });
      setEditing(false); refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to update item"); } finally { setBusy(false); }
  };
  const copy = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (busy) return;
    const data = new FormData(event.currentTarget);
    if (!window.confirm(`Create revision ${String(data.get("revision_code"))} from the selected revision?`)) return;
    setBusy(true); setError("");
    try {
      await api(`/items/${encodeURIComponent(item.id)}/revisions/copy`, "POST", { source_revision_id: String(data.get("source_revision")), revision_code: String(data.get("revision_code")).trim() });
      setCreating(false); refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create revision"); } finally { setBusy(false); }
  };
  const source = revisions.find(entry => entry.id === item.current_revision_id) ?? revisions[0];
  const current = source?.revision_code ?? "";
  const numeric = current.match(/^(.*?)(\d+)$/);
  const suggestedRevision = !current ? "A" : /^[A-Y]$/.test(current) ? String.fromCharCode(current.charCodeAt(0) + 1) : numeric ? `${numeric[1]}${String(Number(numeric[2]) + 1).padStart(numeric[2].length, "0")}` : `${current}-1`;
  const upload = new URLSearchParams({ itemId: item.id, revisionId: item.current_revision_id ?? "", itemNumber: item.item_number, itemName: item.name, itemType: item.item_type, revision: suggestedRevision });
  return <section className="item-actions" aria-label="Item actions">
    <div className="item-actions-buttons"><Button variant="secondary" onClick={() => { setEditing(!editing); setCreating(false); setError(""); }}>Edit item</Button><Button variant="secondary" onClick={() => { setCreating(!creating); setEditing(false); setError(""); }}>Create revision</Button></div>
    {editing && <form className="item-action-form" onSubmit={save}><h2>Edit item</h2><label>Name<input name="name" required maxLength={255} defaultValue={item.name} /></label><label>Category<input name="category" maxLength={100} defaultValue={item.category ?? ""} /></label><label>Description<textarea name="description" maxLength={10000} defaultValue={item.description ?? ""} /></label>{error && <p role="alert" className="error">{error}</p>}<div><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</Button> <Button variant="secondary" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button></div></form>}
    {creating && <div className="item-action-form"><h2>Create revision</h2><p>Choose a source revision to copy or upload new CAD files.</p>{revisions.length > 0 && <form onSubmit={copy}><label>Source revision<select name="source_revision" defaultValue={item.current_revision_id ?? revisions[0].id}>{revisions.map(revision => <option key={revision.id} value={revision.id}>{revision.revision_code}</option>)}</select></label><label>New revision code<input name="revision_code" defaultValue={suggestedRevision} required maxLength={50} pattern="[A-Za-z0-9._-]+" /></label>{error && <p role="alert" className="error">{error}</p>}<Button type="submit" disabled={busy}>{busy ? "Creating…" : "Copy revision"}</Button></form>}<Link className="button button--secondary button-link" to={`/checkin?${upload}`}>Upload new files</Link> <Button variant="ghost" disabled={busy} onClick={() => setCreating(false)}>Close</Button></div>}
  </section>;
}
