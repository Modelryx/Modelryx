import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { ColumnDef } from '@tanstack/react-table';
import {
  api,
  fetchPreviewBlob,
  type AssistantViewerCommand,
  type Item,
  type Revision,
} from '../api';
import { Button } from '../components/Button';
import { DataTable } from '../components/DataTable';
import { ResourceState, useResource } from '../components/ResourceState';
import { ModelPreview, type PreviewEngineeringDetails } from './ModelPreview';

type Occurrence = {
  id: string;
  occurrence_key: string;
  source_reference: string;
  reference_designator: string | null;
  quantity: number;
  is_suppressed: boolean;
  is_visible: boolean;
  child_item: Item;
  child_revision: Revision;
  children: Occurrence[];
};
type Bom = { assembly_item: Item; assembly_revision: Revision; occurrences: Occurrence[] };
type Relationship = {
  id: string;
  relationship_type: string;
  direction: 'reference' | 'referenced_by';
  item_id: string;
  item_number: string;
  item_name: string;
  source_revision_id: string;
  target_revision_id: string | null;
  revision_code: string | null;
  occurrence_count?: number;
};
type WhereUsed = {
  revision_id: string;
  items: {
    occurrence_id: string;
    occurrence_key: string;
    parent_item: Item;
    parent_revision: Revision;
    direct: boolean;
    depth: number;
    total_quantity: number;
    occurrence_count?: number;
  }[];
  warnings: string[];
};
type Representation = {
  id: string;
  kind: '2D' | '3D';
  filename: string;
  media_type: string;
  preview_url: string | null;
  display_name?: string;
  source_artifact_id?: string | null;
  state?: 'READY' | 'FAILED';
  error?: string | null;
  warnings?: string[];
  retry_url?: string | null;
};
function Title({ name, description }: { name: string; description: string }) {
  return (
    <div className="section-heading">
      <div>
        <h2>{name}</h2>
        <p>{description}</p>
      </div>
    </div>
  );
}
function revisionLink(itemId: string, revisionId: string) {
  return `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`;
}
type StructureRow = {
  node: Occurrence;
  depth: number;
  path: string;
  key: string;
  expanded: boolean;
  canExpand: boolean;
};
function StructureTable({
  occurrences,
  revisionId,
  historical,
}: {
  occurrences: Occurrence[];
  revisionId: string;
  historical: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [childrenByRevision, setChildrenByRevision] = useState<Record<string, Occurrence[]>>({});
  const [loadingRevision, setLoadingRevision] = useState('');
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const rows: StructureRow[] = [];
  const visit = (
    nodes: Occurrence[],
    depth: number,
    parentKey: string,
    parentPath: string,
    ancestors: Set<string>
  ) => {
    nodes.forEach((node) => {
      const key = `${parentKey}/${node.id}`;
      const label = node.reference_designator || node.occurrence_key;
      const path = parentPath ? `${parentPath} / ${label}` : label;
      const children = childrenByRevision[node.child_revision.id] ?? node.children;
      const canExpand =
        !historical &&
        node.child_item.item_type === 'ASSEMBLY' &&
        !ancestors.has(node.child_revision.id) &&
        (childrenByRevision[node.child_revision.id] === undefined || children.length > 0);
      const isExpanded = expanded.has(key);
      rows.push({ node, depth, path, key, expanded: isExpanded, canExpand });
      if (isExpanded && canExpand)
        visit(children, depth + 1, key, path, new Set([...ancestors, node.child_revision.id]));
    });
  };
  visit(occurrences, 0, '', '', new Set([revisionId]));
  const toggle = async (entry: StructureRow) => {
    if (entry.expanded) {
      setExpanded((current) => {
        const next = new Set(current);
        next.delete(entry.key);
        return next;
      });
      return;
    }
    const childRevisionId = entry.node.child_revision.id;
    if (childrenByRevision[childRevisionId] === undefined) {
      setLoadingRevision(childRevisionId);
      setError('');
      try {
        const result = await api<Bom>(
          `/items/${encodeURIComponent(entry.node.child_item.id)}/revisions/${encodeURIComponent(childRevisionId)}/bom?depth=1`
        );
        setChildrenByRevision((current) => ({ ...current, [childRevisionId]: result.occurrences }));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : 'Unable to load child assembly');
        return;
      } finally {
        setLoadingRevision('');
      }
    }
    setExpanded((current) => new Set(current).add(entry.key));
  };
  const search = query.trim().toLocaleLowerCase();
  const matches = new Set<string>();
  if (search)
    rows.forEach((row) => {
      if (
        ![
          row.path,
          row.node.child_item.item_number,
          row.node.child_item.name,
          row.node.child_revision.revision_code,
        ].some((value) => value.toLocaleLowerCase().includes(search))
      )
        return;
      let key = row.key;
      while (key) {
        matches.add(key);
        key = key.slice(0, key.lastIndexOf('/'));
      }
    });
  const visibleRows = search ? rows.filter((row) => matches.has(row.key)) : rows;
  const columns: ColumnDef<StructureRow>[] = [
    {
      id: 'occurrence',
      header: 'Occurrence',
      size: 260,
      enableSorting: false,
      enableHiding: false,
      accessorFn: (row) => row.path,
      cell: ({ row }) => {
        const entry = row.original;
        const label = entry.node.reference_designator || entry.node.occurrence_key;
        return (
          <div
            className="structure-cell"
            style={{ paddingLeft: `${Math.min(entry.depth, 12) * 18}px` }}
          >
            <span className="sr-only">
              Level {entry.depth + 1}; path {entry.path}.{' '}
            </span>
            {entry.canExpand ? (
              <button
                type="button"
                className="structure-toggle"
                aria-label={`${entry.expanded ? 'Collapse' : 'Expand'} ${entry.path}`}
                aria-expanded={entry.expanded}
                disabled={loadingRevision === entry.node.child_revision.id}
                onClick={() => void toggle(entry)}
              >
                {loadingRevision === entry.node.child_revision.id
                  ? '…'
                  : entry.expanded
                    ? '▾'
                    : '▸'}
              </button>
            ) : (
              <span className="structure-toggle-spacer" aria-hidden="true" />
            )}
            <span title={entry.path}>{label}</span>
          </div>
        );
      },
    },
    {
      id: 'item',
      header: 'Item number',
      enableSorting: false,
      accessorFn: (row) => row.node.child_item.item_number,
      cell: ({ row }) => (
        <Link
          to={revisionLink(row.original.node.child_item.id, row.original.node.child_revision.id)}
        >
          {row.original.node.child_item.item_number}
        </Link>
      ),
    },
    {
      id: 'name',
      header: 'Name',
      enableSorting: false,
      accessorFn: (row) => row.node.child_item.name,
    },
    {
      id: 'revision',
      header: 'Referenced revision',
      enableSorting: false,
      accessorFn: (row) => row.node.child_revision.revision_code,
      cell: ({ row }) => (
        <Link
          to={revisionLink(row.original.node.child_item.id, row.original.node.child_revision.id)}
        >
          {row.original.node.child_revision.revision_code}
        </Link>
      ),
    },
    {
      id: 'quantity',
      header: 'Quantity',
      enableSorting: false,
      accessorFn: (row) => row.node.quantity,
    },
    { id: 'level', header: 'Level', enableSorting: false, accessorFn: (row) => row.depth + 1 },
    { id: 'path', header: 'Occurrence path', enableSorting: false, accessorFn: (row) => row.path },
    {
      id: 'suppressed',
      header: 'Suppression',
      enableSorting: false,
      accessorFn: (row) => (row.node.is_suppressed ? 'Suppressed' : 'Not suppressed'),
    },
    {
      id: 'visibility',
      header: 'Visibility',
      enableSorting: false,
      accessorFn: (row) => (row.node.is_visible === false ? 'Hidden' : 'Visible'),
    },
  ];
  return (
    <>
      <p className="section-description">
        Expand assemblies to load one level at a time. Search covers loaded occurrences.
      </p>
      <label className="preview-picker">
        Find in loaded structure
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Occurrence, item, or revision"
        />
      </label>
      {error && (
        <p className="section-error" role="alert">
          {error}
        </p>
      )}
      <DataTable
        data={visibleRows}
        columns={columns}
        getRowId={(row) => row.key}
        initialPageSize={25}
        emptyMessage={
          search
            ? 'No loaded occurrences match.'
            : 'No assembly occurrences are recorded for this revision.'
        }
      />
    </>
  );
}
function AssemblyStructure({
  path,
  revisionId,
  historical,
}: {
  path: string;
  revisionId: string;
  historical: boolean;
}) {
  const state = useResource<Bom>(path);
  return (
    <>
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <StructureTable
          occurrences={state.data.occurrences}
          revisionId={revisionId}
          historical={historical}
        />
      )}
    </>
  );
}
export function StructureSection({
  item,
  revisionId,
  snapshotId,
}: {
  item: Item;
  revisionId: string;
  snapshotId?: string;
}) {
  const path = `/items/${encodeURIComponent(item.id)}/revisions/${encodeURIComponent(revisionId)}/bom?depth=1${snapshotId ? `&snapshot_id=${encodeURIComponent(snapshotId)}` : ''}`;
  return (
    <section>
      <Title
        name="Structure"
        description="The assembly hierarchy and the exact revisions referenced by each occurrence."
      />
      {snapshotId && (
        <p className="section-description">
          Historical structure shows this assembly version&apos;s direct occurrences. Child
          assemblies are not expanded because their content versions were not pinned.
        </p>
      )}
      {item.item_type === 'ASSEMBLY' ? (
        <AssemblyStructure path={path} revisionId={revisionId} historical={!!snapshotId} />
      ) : (
        <p className="empty-panel">
          Structure is available for assembly revisions. Use Relationships to see where this
          revision is used.
        </p>
      )}
    </section>
  );
}
function RelationshipTable({
  rows,
  direction,
}: {
  rows: Relationship[];
  direction: 'reference' | 'referenced_by';
}) {
  const columns: ColumnDef<Relationship>[] = [
    {
      accessorKey: 'item_number',
      header: 'Item number',
      cell: ({ row }) => {
        const relatedRevision =
          row.original.direction === 'reference'
            ? row.original.target_revision_id
            : row.original.source_revision_id;
        return (
          <Link
            to={
              relatedRevision
                ? revisionLink(row.original.item_id, relatedRevision)
                : `/items/${encodeURIComponent(row.original.item_id)}`
            }
          >
            {row.original.item_number}
          </Link>
        );
      },
    },
    { accessorKey: 'item_name', header: 'Name' },
    {
      id: 'revision',
      header: 'Referenced revision',
      accessorFn: (row) => row.revision_code || '—',
    },
    { accessorKey: 'relationship_type', header: 'Relationship' },
    { id: 'count', header: 'Occurrences', accessorFn: (row) => row.occurrence_count ?? 1 },
  ];
  return (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(row) => row.id}
      emptyMessage={direction === 'reference' ? 'No references.' : 'No incoming references.'}
    />
  );
}
function References({ path }: { path: string }) {
  const state = useResource<Relationship[]>(path);
  return (
    <>
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <div className="relationship-sections">
          <section>
            <h3>References</h3>
            <RelationshipTable
              rows={state.data.filter((row) => row.direction === 'reference')}
              direction="reference"
            />
          </section>
          <section>
            <h3>Referenced by</h3>
            <RelationshipTable
              rows={state.data.filter((row) => row.direction === 'referenced_by')}
              direction="referenced_by"
            />
          </section>
        </div>
      )}
    </>
  );
}
function WhereUsedSection({ path }: { path: string }) {
  const state = useResource<WhereUsed>(path);
  const columns: ColumnDef<WhereUsed['items'][number]>[] = [
    {
      id: 'item',
      header: 'Parent item',
      accessorFn: (row) => row.parent_item.item_number,
      cell: ({ row }) => (
        <Link to={revisionLink(row.original.parent_item.id, row.original.parent_revision.id)}>
          {row.original.parent_item.item_number}
        </Link>
      ),
    },
    { id: 'name', header: 'Name', accessorFn: (row) => row.parent_item.name },
    {
      id: 'revision',
      header: 'Parent revision',
      accessorFn: (row) => row.parent_revision.revision_code,
    },
    {
      id: 'relationship',
      header: 'Relationship',
      accessorFn: (row) => (row.direct ? 'Direct' : 'Indirect'),
    },
    { accessorKey: 'depth', header: 'Levels' },
    { accessorKey: 'total_quantity', header: 'Quantity' },
    { id: 'paths', header: 'Paths', accessorFn: (row) => row.occurrence_count ?? 1 },
  ];
  return (
    <section>
      <h3>Where used</h3>
      <p className="section-description">Parent assemblies that reference this exact revision.</p>
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <>
          {state.data.warnings.map((warning, index) => (
            <p className="warning" key={`${index}-${warning}`}>
              {warning}
            </p>
          ))}
          <DataTable
            data={state.data.items}
            columns={columns}
            getRowId={(row, index) => `${row.occurrence_id}:${index}`}
            emptyMessage="No parent assemblies reference this revision."
          />
        </>
      )}
    </section>
  );
}
export function RelationshipsSection({
  itemId,
  revisionId,
}: {
  itemId: string;
  revisionId: string;
}) {
  const root = `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`;
  return (
    <section>
      <Title
        name="Relationships"
        description="References and where-used relationships for this revision."
      />
      <References path={`${root}/relationships`} />
      <WhereUsedSection path={`${root}/where-used`} />
    </section>
  );
}
function RepresentationViewer({ representation }: { representation: Representation }) {
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setUrl(undefined);
    setError('');
    setLoading(true);
    const run = async () => {
      try {
        if (!representation.preview_url) throw new Error('Preview is unavailable.');
        const blob = await fetchPreviewBlob(representation.preview_url, controller.signal);
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : 'Unable to load preview');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void run();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [representation.id, representation.preview_url, attempt]);
  return (
    <>
      {loading && <p role="status">Loading preview…</p>}
      {error && (
        <div className="section-error" role="alert">
          {error}
          <Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </Button>
        </div>
      )}
      {url && representation.media_type === 'application/pdf' ? (
        <iframe
          className="document-preview"
          title={`Preview ${representation.filename}`}
          src={url}
        />
      ) : url && representation.media_type === 'image/png' ? (
        <img className="image-preview" alt={`Preview of ${representation.filename}`} src={url} />
      ) : url ? (
        <p className="empty-panel">This representation format cannot be displayed here.</p>
      ) : null}
    </>
  );
}
function FullscreenPreview({
  children,
  media,
}: {
  children: ReactNode;
  media: 'image' | 'full-width';
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const update = () => setFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener('fullscreenchange', update);
    return () => document.removeEventListener('fullscreenchange', update);
  }, []);
  const toggle = async () => {
    setError('');
    try {
      if (document.fullscreenElement === containerRef.current) await document.exitFullscreen();
      else if (containerRef.current?.requestFullscreen)
        await containerRef.current.requestFullscreen();
      else throw new Error('Full screen is not available in this browser.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to change full-screen mode.');
    }
  };
  return (
    <div
      className={`preview-fullscreen${media === 'image' ? ' preview-fullscreen--image' : ''}`}
      ref={containerRef}
    >
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="preview-fullscreen-content">
        {children}
        <div className="preview-fullscreen-toolbar">
          <button
            type="button"
            className="preview-fullscreen-button"
            onClick={() => void toggle()}
            aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
            title={fullscreen ? 'Exit full screen' : 'Full screen'}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {fullscreen ? (
                <path d="M4 9h5V4M20 9h-5V4M4 15h5v5M20 15h-5v5" />
              ) : (
                <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
              )}
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
function AssemblyModelPreview({
  item,
  revisionId,
  revisionCode,
  representation,
  snapshotId,
  onManagedSelection,
  viewerCommand,
}: {
  item: Item;
  revisionId: string;
  revisionCode: string;
  representation: Representation;
  snapshotId?: string;
  onManagedSelection?: (id?: string) => void;
  viewerCommand?: AssistantViewerCommand;
}) {
  const suffix = snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : '';
  const [selectedPreviewIds, setSelectedPreviewIds] = useState<string[]>([]);
  const structure = useResource<Bom>(
    `/items/${encodeURIComponent(item.id)}/revisions/${encodeURIComponent(revisionId)}/bom?depth=1${snapshotId ? `&snapshot_id=${encodeURIComponent(snapshotId)}` : ''}`
  );
  const mapping = useResource<{
    matches: Record<string, string>;
    ambiguous: string[];
    unmapped: string[];
  }>(
    `/items/${encodeURIComponent(item.id)}/revisions/${encodeURIComponent(revisionId)}/preview/occurrence-map${suffix}`
  );
  const selectedManagedId =
    selectedPreviewIds.length === 1
      ? Object.entries(mapping.data?.matches ?? {}).find(
          ([, previewId]) => previewId === selectedPreviewIds[0]
        )?.[0]
      : undefined;
  useEffect(() => {
    onManagedSelection?.(selectedManagedId);
  }, [onManagedSelection, selectedManagedId]);
  useEffect(() => () => onManagedSelection?.(undefined), [onManagedSelection]);
  const mappedIds =
    viewerCommand?.occurrence_ids
      .map((id) => mapping.data?.matches[id])
      .filter((id): id is string => !!id) ?? [];
  const mappedCommand =
    viewerCommand && mapping.data && mappedIds.length
      ? { ...viewerCommand, occurrence_ids: mappedIds }
      : undefined;
  const unmappedCommandCount =
    viewerCommand && mapping.data ? viewerCommand.occurrence_ids.length - mappedIds.length : 0;
  const details = new Map<string, PreviewEngineeringDetails>();
  const visit = (nodes: Occurrence[]) =>
    nodes.forEach((node) => {
      const id = mapping.data?.matches[node.id];
      if (id) {
        details.set(id, {
          itemNumber: node.child_item.item_number,
          itemName: node.child_item.name,
          revisionCode: node.child_revision.revision_code,
          revisionPath: revisionLink(node.child_item.id, node.child_revision.id),
          referenceDesignator: node.reference_designator,
          occurrenceKey: node.occurrence_key,
          sourceReference: node.source_reference,
          quantity: node.quantity,
          isSuppressed: node.is_suppressed,
          isVisible: node.is_visible,
        });
      }
      visit(node.children);
    });
  visit(structure.data?.occurrences ?? []);
  return (
    <>
      {mapping.error && (
        <p className="section-description">
          Engineering properties could not be linked to the preview.{' '}
          <button type="button" onClick={mapping.retry}>
            Retry mapping
          </button>
        </p>
      )}
      {mapping.data && (mapping.data.ambiguous.length > 0 || mapping.data.unmapped.length > 0) && (
        <p className="section-description">
          Direct occurrences: {Object.keys(mapping.data.matches).length} linked to the 3D preview;{' '}
          {mapping.data.ambiguous.length} ambiguous and {mapping.data.unmapped.length} unmapped.
        </p>
      )}
      {unmappedCommandCount > 0 && (
        <p className="section-description" role="status">
          {unmappedCommandCount} suggested occurrence{unmappedCommandCount === 1 ? '' : 's'} cannot
          be linked to this preview.
        </p>
      )}
      <ModelPreview
        revisionId={revisionId}
        revisionCode={revisionCode}
        filename={representation.filename}
        modelUrl={representation.preview_url!}
        snapshotId={snapshotId}
        onSelectionChange={setSelectedPreviewIds}
        viewerCommand={mappedCommand}
        engineeringDetails={details}
      />
    </>
  );
}

export function PreviewSection({
  revisionId,
  revisionCode,
  item,
  snapshotId,
  onManagedSelection,
  viewerCommand,
}: {
  revisionId: string;
  revisionCode: string;
  item: Item;
  snapshotId?: string;
  onManagedSelection?: (id?: string) => void;
  viewerCommand?: AssistantViewerCommand;
}) {
  const state = useResource<Representation[]>(
    `/revisions/${encodeURIComponent(revisionId)}/preview/representations${snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ''}`
  );
  const [selected, setSelected] = useState('');
  const [retrying, setRetrying] = useState('');
  const [retryError, setRetryError] = useState('');
  const ready = (state.data ?? []).filter((item) => item.state !== 'FAILED' && item.preview_url);
  const current = ready.find((item) => item.id === selected) ?? ready[0];
  const failed = (state.data ?? []).filter((item) => item.state === 'FAILED');
  useEffect(() => {
    if (current?.kind !== '3D') onManagedSelection?.(undefined);
  }, [current?.kind, onManagedSelection]);
  return (
    <section>
      <div className="section-heading">
        <h2>Preview</h2>
      </div>
      {snapshotId && (
        <p className="section-description">
          Showing representations stored with the selected historical version.
        </p>
      )}
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <>
          {retryError && (
            <p role="alert" className="error">
              {retryError}
            </p>
          )}
          {failed.map((entry) => (
            <p className="warning" key={entry.id}>
              {entry.display_name || entry.filename}: {entry.error || 'Preview generation failed.'}{' '}
              {!snapshotId && entry.retry_url && (
                <Button
                  variant="secondary"
                  disabled={!!retrying}
                  onClick={async () => {
                    setRetrying(entry.id);
                    setRetryError('');
                    try {
                      await api(entry.retry_url!.replace(/^\/api\/v1(?=\/)/, ''), 'POST');
                      state.retry();
                    } catch (reason) {
                      setRetryError(
                        reason instanceof Error ? reason.message : 'Unable to retry preview'
                      );
                    } finally {
                      setRetrying('');
                    }
                  }}
                >
                  {retrying === entry.id ? 'Queueing…' : 'Retry preview'}
                </Button>
              )}
            </p>
          ))}
          {current ? (
            <>
              <label className="preview-picker">
                Representation
                <select value={current.id} onChange={(event) => setSelected(event.target.value)}>
                  {ready.map((entry) => (
                    <option value={entry.id} key={entry.id}>
                      {entry.kind} · {entry.display_name || entry.filename}
                    </option>
                  ))}
                </select>
              </label>
              <FullscreenPreview
                media={
                  current.kind === '2D' && current.media_type === 'image/png'
                    ? 'image'
                    : 'full-width'
                }
              >
                {current.kind === '3D' && current.preview_url ? (
                  item.item_type === 'ASSEMBLY' ? (
                    <AssemblyModelPreview
                      key={current.id}
                      item={item}
                      revisionId={revisionId}
                      revisionCode={revisionCode}
                      representation={current}
                      snapshotId={snapshotId}
                      onManagedSelection={onManagedSelection}
                      viewerCommand={viewerCommand}
                    />
                  ) : (
                    <ModelPreview
                      key={current.id}
                      revisionId={revisionId}
                      revisionCode={revisionCode}
                      filename={current.filename}
                      modelUrl={current.preview_url}
                      snapshotId={snapshotId}
                    />
                  )
                ) : (
                  <RepresentationViewer key={current.id} representation={current} />
                )}
              </FullscreenPreview>
            </>
          ) : (
            <p className="empty-panel">
              No ready preview representation is available for this version.
            </p>
          )}
        </>
      )}
    </section>
  );
}
