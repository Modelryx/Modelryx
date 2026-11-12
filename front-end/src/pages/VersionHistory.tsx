import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { type RevisionArtifact } from "../api";
import { DataTable } from "../components/DataTable";
import { ResourceState, useResource } from "../components/ResourceState";
import { VersionComparison } from "./VersionComparison";

type Snapshot = { id: string; sequence: number; comment: string | null; created_at: string; artifact_count: number };
type FileRow = { key: string; label: string; sha256: string; state: string };
type Difference = { key: string; label: string; change: "Added" | "Removed" | "Changed"; before: string; after: string };

function files(relations: RevisionArtifact[]): FileRow[] {
  const rows = relations.flatMap(relation => relation.artifact.files.map(file => ({
    key: `${relation.relation_type}/${relation.artifact.name}/${file.role ?? ""}/${file.reference_name ?? file.original_filename}`,
    label: `${relation.artifact.name} / ${file.reference_name ?? file.original_filename}`,
    sha256: file.sha256,
    state: file.state,
  })));
  rows.sort((a, b) => a.key.localeCompare(b.key) || a.sha256.localeCompare(b.sha256));
  const seen = new Map<string, number>();
  return rows.map(row => {
    const index = seen.get(row.key) ?? 0;
    seen.set(row.key, index + 1);
    return { ...row, key: `${row.key}#${index}` };
  });
}

function differences(before: FileRow[], after: FileRow[]): Difference[] {
  const left = new Map(before.map(row => [row.key, row]));
  const right = new Map(after.map(row => [row.key, row]));
  return [...new Set([...left.keys(), ...right.keys()])].flatMap(key => {
    const oldRow = left.get(key);
    const newRow = right.get(key);
    if (oldRow?.sha256 === newRow?.sha256) return [];
    return [{ key, label: newRow?.label ?? oldRow?.label ?? key, change: !oldRow ? "Added" as const : !newRow ? "Removed" as const : "Changed" as const, before: oldRow?.sha256.slice(0, 12) ?? "—", after: newRow?.sha256.slice(0, 12) ?? "—" }];
  });
}

export function VersionHistory({ revisionId, itemId, canRestore, isAssembly }: { revisionId: string; itemId: string; canRestore: boolean; isAssembly: boolean }) {
  const versions = useResource<Snapshot[]>(`/item-revisions/${encodeURIComponent(revisionId)}/content-snapshots`);
  const [params, setParams] = useSearchParams();
  const [compareId, setCompareId] = useState("");
  const current = versions.data?.reduce<Snapshot | undefined>((latest, row) => !latest || row.sequence > latest.sequence ? row : latest, undefined);
  const selected = versions.data?.find(row => row.id === params.get("version")) ?? current;
  const other = versions.data?.find(row => row.id === compareId && row.id !== selected?.id)
    ?? (selected?.id === current?.id ? versions.data?.find(row => row.id !== current?.id) : current);
  const selectedFiles = useResource<RevisionArtifact[]>(selected ? `/item-revisions/${encodeURIComponent(revisionId)}/artifacts?snapshot_id=${encodeURIComponent(selected.id)}` : null);
  const comparisonFiles = useResource<RevisionArtifact[]>(other ? `/item-revisions/${encodeURIComponent(revisionId)}/artifacts?snapshot_id=${encodeURIComponent(other.id)}` : null);
  const diff = selectedFiles.data && comparisonFiles.data ? differences(files(comparisonFiles.data), files(selectedFiles.data)) : [];
  const select = (id: string) => { const next = new URLSearchParams(params); next.set("version", id); setParams(next); setCompareId(""); };
  const columns: ColumnDef<Snapshot>[] = [
    { id: "sequence", header: "Version", accessorKey: "sequence", enableHiding: false, cell: ({ row }) => <button type="button" className="version-link" aria-current={selected?.id === row.original.id ? "true" : undefined} onClick={() => select(row.original.id)}>Version {row.original.sequence}{row.original.id === current?.id ? " · Current" : ""}</button> },
    { accessorKey: "created_at", header: "Committed", cell: ({ getValue }) => new Date(String(getValue())).toLocaleString() },
    { accessorKey: "artifact_count", header: "Artifacts" },
    { accessorKey: "comment", header: "Comment", cell: ({ getValue }) => String(getValue() || "—") },
    ...(canRestore ? [{ id: "restore", header: "Actions", cell: ({ row }: { row: { original: Snapshot } }) => row.original.id !== current?.id ? <Link to={`/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}?${new URLSearchParams({ section: "edit", sourceSnapshotId: row.original.id })}`}>Restore through staging</Link> : null }] : []),
  ];
  const artifactColumns: ColumnDef<FileRow>[] = [
    { accessorKey: "label", header: "Artifact / file", enableHiding: false },
    { accessorKey: "state", header: "File state" },
    { id: "sha256", header: "SHA-256", accessorFn: row => row.sha256.slice(0, 16) },
  ];
  const diffColumns: ColumnDef<Difference>[] = [
    { accessorKey: "label", header: "Artifact / file", enableHiding: false },
    { accessorKey: "change", header: "Change" },
    { accessorKey: "before", header: "Compared version hash" },
    { accessorKey: "after", header: "Selected version hash" },
  ];
  return <section className="version-history"><div className="section-heading"><div><h2>Versions</h2><p>Immutable content snapshots within this revision.</p></div></div>
    <ResourceState state={versions} />
    {!versions.loading && !versions.error && versions.data && <>
      <DataTable data={versions.data} columns={columns} getRowId={row => row.id} emptyMessage="This revision has no committed versions." />
      {selected && <section className="version-detail" aria-label={`Version ${selected.sequence} details`}>
        <div className="section-heading"><div><h3>Version {selected.sequence}{selected.id === current?.id ? " · Current" : " · Historical"}</h3><p>{selected.comment || "No commit comment"} · {new Date(selected.created_at).toLocaleString()}</p></div></div>
        <p className="section-description">These files, previews, and direct assembly occurrences belong to the selected content snapshot. Lifecycle actions still apply to the current revision.</p>
        <p className="version-detail-links"><Link to={`/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}?${new URLSearchParams({ section: "preview", version: selected.id })}`}>View this version's preview</Link> · <Link to={`/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}?${new URLSearchParams({ section: "structure", version: selected.id })}`}>View this version's structure</Link> · <Link to={`/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}?${new URLSearchParams({ section: "revision-files", version: selected.id })}`}>View artifacts</Link></p>
        <ResourceState state={selectedFiles} />
        {!selectedFiles.loading && !selectedFiles.error && selectedFiles.data && <DataTable data={files(selectedFiles.data)} columns={artifactColumns} getRowId={row => row.key} emptyMessage="No files are recorded in this version." />}
        {other && <div className="version-comparison"><label>Compare with <select value={other.id} onChange={event => setCompareId(event.target.value)}>{versions.data.filter(row => row.id !== selected.id).map(row => <option value={row.id} key={row.id}>Version {row.sequence}{row.id === current?.id ? " (current)" : ""}</option>)}</select></label><p className="section-description">File changes use names, roles, references, and SHA-256.</p><ResourceState state={comparisonFiles} />{!selectedFiles.loading && !comparisonFiles.loading && !selectedFiles.error && !comparisonFiles.error && <DataTable data={diff} columns={diffColumns} getRowId={row => row.key} emptyMessage="No file differences between these versions." />}<VersionComparison key={`${other.id}:${selected.id}`} revisionId={revisionId} beforeId={other.id} afterId={selected.id} isAssembly={isAssembly} /></div>}
      </section>}
    </>}
  </section>;
}
