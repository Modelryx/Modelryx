import { lazy, Suspense, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Button } from "../components/Button";
import { DataTable } from "../components/DataTable";
import { ResourceState, useResource } from "../components/ResourceState";

const ComparisonViewer = lazy(() => import("./Comparison").then(module => ({ default: module.ComparisonViewer })));

type Occurrence = {
  occurrence_key: string;
  quantity: number;
  is_suppressed: boolean;
  child_item: { item_number: string };
  child_revision: { id: string; revision_code: string };
};
type Bom = { occurrences: Occurrence[] };
type Change = { key: string; change: "Added" | "Removed" | "Changed"; before: string; after: string };

function description(row?: Occurrence): string {
  if (!row) return "—";
  return `${row.child_item.item_number} · Revision ${row.child_revision.revision_code} · Qty ${row.quantity}${row.is_suppressed ? " · Suppressed" : ""}`;
}

export function VersionComparison({ revisionId, beforeId, afterId, isAssembly }: {
  revisionId: string; beforeId: string; afterId: string; isAssembly: boolean;
}) {
  const [show3D, setShow3D] = useState(false);
  const bomPath = (snapshotId: string) =>
    `/revisions/${encodeURIComponent(revisionId)}/bom?depth=1&snapshot_id=${encodeURIComponent(snapshotId)}`;
  const before = useResource<Bom>(isAssembly ? bomPath(beforeId) : null);
  const after = useResource<Bom>(isAssembly ? bomPath(afterId) : null);
  const left = new Map((before.data?.occurrences ?? []).map(row => [row.occurrence_key, row]));
  const right = new Map((after.data?.occurrences ?? []).map(row => [row.occurrence_key, row]));
  const changes: Change[] = [...new Set([...left.keys(), ...right.keys()])].sort().flatMap(key => {
    const oldRow = left.get(key);
    const newRow = right.get(key);
    if (oldRow && newRow && oldRow.child_revision.id === newRow.child_revision.id
      && oldRow.quantity === newRow.quantity && oldRow.is_suppressed === newRow.is_suppressed) return [];
    return [{ key, change: !oldRow ? "Added" as const : !newRow ? "Removed" as const : "Changed" as const,
      before: description(oldRow), after: description(newRow) }];
  });
  const columns: ColumnDef<Change>[] = [
    { accessorKey: "key", header: "Occurrence", enableHiding: false },
    { accessorKey: "change", header: "Change" },
    { accessorKey: "before", header: "Compared version" },
    { accessorKey: "after", header: "Selected version" },
  ];
  return <section className="version-comparison" aria-label="Engineering version comparison">
    <h4>Engineering comparison</h4>
    <p className="section-description">The BOM comparison covers direct occurrences in the selected assembly. A 3D overlay shows the two stored representations when both are available; it does not certify geometry compatibility.</p>
    {isAssembly && <><ResourceState state={before} /><ResourceState state={after} />
      {!before.loading && !after.loading && !before.error && !after.error &&
        <DataTable data={changes} columns={columns} getRowId={row => row.key}
          emptyMessage="No direct occurrence changes between these versions." />}</>}
    <Button variant="secondary" onClick={() => setShow3D(value => !value)}>
      {show3D ? "Close 3D comparison" : "Compare 3D representations"}
    </Button>
    {show3D && <Suspense fallback={<p role="status">Loading 3D comparison…</p>}>
      <ComparisonViewer key={`${beforeId}:${afterId}`} fromId={revisionId} toId={revisionId}
        fromSnapshotId={beforeId} toSnapshotId={afterId} bomChanges={[]} />
    </Suspense>}
  </section>;
}
