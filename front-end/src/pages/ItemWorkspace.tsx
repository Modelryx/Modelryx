import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { ColumnDef } from '@tanstack/react-table';
import {
  api,
  downloadManagedFile,
  fetchPreviewBlob,
  type AssistantViewerCommand,
  type Item,
  type Revision,
  type Attachment,
  type RevisionArtifact,
  type RevisionMetadata,
} from '../api';
import { Button } from '../components/Button';
import { DataTable } from '../components/DataTable';
import { ResourceState, useResource } from '../components/ResourceState';
import { StructureSection, RelationshipsSection, PreviewSection } from './RevisionViews';
import { ItemActions } from './ItemActions';
import {
  RevisionLifecycle,
  RevisionLifecyclePanel,
  type RevisionLifecycleAction,
} from './RevisionLifecycle';
import type { ProcessingJob as StageJob } from './revisionProperties/types';
import { StagesPanel } from './Stages';
import { BOMIntelligenceSection } from './revisionProperties/BOMIntelligenceSection';
import { PropertiesSection } from './revisionProperties/PropertiesSection';
import { VersionHistory } from './VersionHistory';
import { RevisionIndexTabs } from './RevisionIndexTabs';
import { GeneratePreviewPage } from './GeneratePreview';
import { RevisionAssistant } from './RevisionAssistant';
import './revisionProperties/properties.css';

const Comparison = lazy(() =>
  import('./Comparison').then((module) => ({ default: module.ComparisonPage }))
);
const CheckinWorkflow = lazy(() =>
  import('./checkinNew/CheckinWorkflow').then((module) => ({ default: module.CheckinWorkflow }))
);
const ReviewInbox = lazy(() =>
  import('./ReviewInbox').then((module) => ({ default: module.ReviewInbox }))
);

function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    </div>
  );
}
function OverviewProperties({ item, revision }: { item: Item; revision?: Revision }) {
  const rows = [
    ['Item number', item.item_number],
    ['Name', item.name],
    ['Type', item.item_type],
    ['Item status', item.status],
    ['Description', item.description || 'Not provided'],
    ...(revision
      ? [
          ['Revision', revision.revision_code],
          ['Lifecycle', revision.lifecycle_state],
          ['Processing', revision.state],
          ['Checkout', revision.checkout_status],
          ['CAD system', revision.cad_system || '—'],
          ['Native format', revision.native_format || '—'],
        ]
      : []),
  ];
  return (
    <div className="overview-properties-scroll">
      <table className="overview-properties">
        <thead>
          <tr>
            <th scope="col">Parameter</th>
            <th scope="col">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function RevisionThumbnail({
  revisionId,
  itemNumber,
  revisionCode,
  previewPath,
  snapshotId,
}: {
  revisionId: string;
  itemNumber: string;
  revisionCode: string;
  previewPath: string;
  snapshotId?: string;
}) {
  const [url, setUrl] = useState('');
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable' | 'error'>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setUrl('');
    setStatus('loading');
    void fetchPreviewBlob(
      `/revisions/${encodeURIComponent(revisionId)}/preview/thumbnail?appearance=2${snapshotId ? `&snapshot_id=${encodeURIComponent(snapshotId)}` : ''}`,
      controller.signal
    )
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        setStatus('ready');
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setStatus(
            reason instanceof Error && reason.message === 'Revision thumbnail is unavailable'
              ? 'unavailable'
              : 'error'
          );
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [revisionId, snapshotId, attempt]);
  return (
    <figure className="revision-thumbnail">
      {status === 'ready' ? (
        <img src={url} alt={`${itemNumber} revision ${revisionCode} model thumbnail`} />
      ) : (
        <div
          className="revision-thumbnail-placeholder"
          role={status === 'error' ? 'alert' : 'status'}
        >
          {status === 'loading' ? (
            'Loading revision image…'
          ) : status === 'error' ? (
            <span>
              Could not load the revision image.{' '}
              <Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
                Retry
              </Button>
            </span>
          ) : (
            'No revision image yet'
          )}
        </div>
      )}
      <figcaption>
        {status === 'ready'
          ? snapshotId
            ? "Selected version's 3D representation"
            : 'Current 3D representation'
          : status === 'unavailable'
            ? 'An image appears after 3D processing completes.'
            : 'Revision image'}{' '}
        <Link to={previewPath}>Open preview</Link>
      </figcaption>
    </figure>
  );
}
function fileSize(bytes: number) {
  if (!Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
function DownloadButton({ path, filename }: { path: string | null; filename: string }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <div className="download-control">
      <Button
        variant="secondary"
        disabled={!path || busy}
        onClick={async () => {
          if (!path) return;
          setBusy(true);
          setError('');
          try {
            await downloadManagedFile(path, filename);
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Download failed');
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? 'Downloading…' : 'Download'}
      </Button>
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
function Attachments({
  path,
  scope,
  editUrl,
}: {
  path: string;
  scope: 'Item' | 'Revision';
  editUrl?: string;
}) {
  const state = useResource<Attachment[]>(path);
  const columns: ColumnDef<Attachment>[] = [
    { accessorKey: 'name', header: 'Name', enableHiding: false },
    { accessorKey: 'attachment_type', header: 'Type' },
    { accessorKey: 'lifecycle_state', header: 'Attachment status' },
    {
      id: 'latest-state',
      header: 'Latest version state',
      accessorFn: (row) => {
        const latest = row.versions?.reduce<(typeof row.versions)[number] | undefined>(
          (current, version) =>
            !current || version.version_number > current.version_number ? version : current,
          undefined
        );
        return latest ? `${latest.state} (v${latest.version_number})` : 'No versions';
      },
    },
    {
      id: 'filename',
      header: 'Latest ready file',
      accessorFn: (row) => row.latest_ready_version?.filename || '—',
    },
    {
      id: 'version',
      header: 'Version',
      accessorFn: (row) => row.latest_ready_version?.version_number ?? '—',
    },
    {
      id: 'download',
      header: 'Download',
      enableSorting: false,
      cell: ({ row }) => (
        <DownloadButton
          path={row.original.latest_ready_version?.download_url ?? null}
          filename={row.original.latest_ready_version?.filename ?? row.original.name}
        />
      ),
    },
  ];
  return (
    <section id={scope === 'Item' ? 'item-attachments' : 'revision-attachments'}>
      <SectionTitle
        title={scope === 'Revision' ? 'Attachments' : 'Item attachments'}
        description={
          scope === 'Item'
            ? 'Additional files associated with the Item across revisions.'
            : 'Additional files associated with this revision.'
        }
      />
      {editUrl && (
        <p>
          <Link className="button button--secondary button-link" to={editUrl}>
            Manage attachments
          </Link>
        </p>
      )}
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <DataTable
          data={state.data}
          columns={columns}
          getRowId={(row) => row.id}
          emptyMessage={scope === 'Revision' ? 'No attachments.' : 'No item attachments.'}
        />
      )}
    </section>
  );
}
type ArtifactRow = {
  id: string;
  artifactName: string;
  artifactType: string;
  classification: string;
  relationType: string;
  source: string;
  generator: string | null;
  derivedFrom: string | null;
  artifactState: string;
  filename: string | null;
  role: string | null;
  referenceName: string | null;
  bytes: number | null;
  state: string | null;
  downloadUrl: string | null;
};
function ArtifactSection({
  path,
  revisionId,
  itemNumber,
  revisionCode,
  isAssembly,
  snapshotId,
}: {
  path: string;
  revisionId: string;
  itemNumber: string;
  revisionCode: string;
  isAssembly: boolean;
  snapshotId?: string;
}) {
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [selectedVersion, setSelectedVersion] = useState('');
  const versions = useResource<ContentSnapshot[]>(
    `/item-revisions/${encodeURIComponent(revisionId)}/content-snapshots`
  );
  const currentVersion = versions.data?.reduce<ContentSnapshot | undefined>(
    (current, version) => (!current || version.sequence > current.sequence ? version : current),
    undefined
  );
  const selectedSnapshot =
    versions.data?.find((version) => version.id === selectedVersion) ??
    versions.data?.find((version) => version.id === snapshotId) ??
    currentVersion;
  const downloadAll = async () => {
    if (downloadBusy || !selectedSnapshot || versions.loading || versions.error) return;
    setDownloadBusy(true);
    setDownloadError('');
    try {
      let job = await api<{
        id: string;
        snapshot_id: string | null;
        state: string;
        error_detail: string | null;
      }>(
        `/revisions/${encodeURIComponent(revisionId)}/download-bundles?${new URLSearchParams({ snapshot_id: selectedSnapshot.id })}`,
        'POST'
      );
      if (job.snapshot_id !== selectedSnapshot.id)
        throw new Error(
          'The server did not select the requested version. Update and restart the server before downloading.'
        );
      for (let attempt = 0; job.state !== 'READY' && attempt < 40; attempt++) {
        if (job.state === 'FAILED')
          throw new Error(job.error_detail || 'CAD package creation failed');
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        job = await api(`/download-bundles/${encodeURIComponent(job.id)}`);
      }
      if (job.state !== 'READY')
        throw new Error('The CAD package is still being prepared. Continue from Workspace.');
      await downloadManagedFile(
        `/api/v1/download-bundles/${encodeURIComponent(job.id)}/download`,
        `${itemNumber}-${revisionCode}-v${selectedSnapshot.sequence}-cad-files.zip`
      );
    } catch (reason) {
      setDownloadError(reason instanceof Error ? reason.message : 'Unable to download CAD files');
    } finally {
      setDownloadBusy(false);
    }
  };
  const state = useResource<RevisionArtifact[]>(path);
  const rows: ArtifactRow[] = (state.data ?? []).flatMap<ArtifactRow>((relation) => {
    const artifact = relation.artifact;
    const shared = {
      artifactName: artifact.name,
      artifactType: artifact.artifact_type,
      classification: artifact.classification,
      relationType: relation.relation_type,
      source: artifact.source,
      generator: artifact.generator,
      derivedFrom: artifact.derived_from_artifact_id,
      artifactState: artifact.state,
    };
    return artifact.files.length
      ? artifact.files.map((file) => ({
          ...shared,
          id: `${relation.relation_id}:${file.id}`,
          filename: file.original_filename,
          role: file.role,
          referenceName: file.reference_name,
          bytes: file.byte_size,
          state: file.state,
          downloadUrl: file.download_url,
        }))
      : [
          {
            ...shared,
            id: relation.relation_id,
            filename: null,
            role: null,
            referenceName: null,
            bytes: null,
            state: null,
            downloadUrl: null,
          },
        ];
  });
  const columns: ColumnDef<ArtifactRow>[] = [
    {
      id: 'file',
      header: 'File',
      accessorFn: (row) => row.filename || 'No file available',
      enableHiding: false,
    },
    { accessorKey: 'artifactName', header: 'Artifact' },
    { accessorKey: 'artifactType', header: 'Artifact type' },
    { id: 'role', header: 'File role', accessorFn: (row) => row.role || '—' },
    { id: 'reference', header: 'Reference', accessorFn: (row) => row.referenceName || '—' },
    { id: 'state', header: 'File state', accessorFn: (row) => row.state || '—' },
    { accessorKey: 'artifactState', header: 'Artifact state' },
    { accessorKey: 'relationType', header: 'Relation' },
    { accessorKey: 'source', header: 'Source' },
    { id: 'generator', header: 'Generator', accessorFn: (row) => row.generator || '—' },
    {
      id: 'derived-from',
      header: 'Derived from artifact',
      accessorFn: (row) => row.derivedFrom || '—',
    },
    {
      id: 'size',
      header: 'Size',
      accessorFn: (row) => row.bytes,
      cell: ({ row }) => (row.original.bytes === null ? '—' : fileSize(row.original.bytes)),
    },
    {
      id: 'download',
      header: 'Download',
      enableSorting: false,
      cell: ({ row }) => (
        <DownloadButton
          path={row.original.downloadUrl}
          filename={row.original.filename ?? row.original.artifactName}
        />
      ),
    },
  ];
  const groups = [
    {
      key: 'AUTHORITATIVE',
      label: 'Authoritative artifacts',
      description: 'Managed source artifacts and their files.',
    },
    {
      key: 'DERIVED',
      label: 'Derived artifacts',
      description: 'Generated representations and other derived outputs.',
    },
    {
      key: 'EXTERNAL_REFERENCE',
      label: 'External references',
      description: 'References recorded with this revision.',
    },
  ];
  return (
    <section id="revision-files">
      <SectionTitle
        title="Artifacts"
        description="Files belong to the selected revision and are grouped by artifact classification."
      />
      <div className="artifact-download-controls">
        <label>
          CAD package version
          <select
            value={selectedSnapshot?.id ?? ''}
            disabled={downloadBusy || versions.loading || !!versions.error || !currentVersion}
            onChange={(event) => setSelectedVersion(event.target.value)}
          >
            {!currentVersion && <option value="">No versions available</option>}
            {(versions.data ?? []).map((version) => (
              <option key={version.id} value={version.id}>
                Version {version.sequence}
                {version.id === currentVersion?.id ? ' (current)' : ''}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="secondary"
          disabled={downloadBusy || versions.loading || !!versions.error || !selectedSnapshot}
          onClick={() => void downloadAll()}
        >
          {downloadBusy ? 'Preparing package…' : 'Download all CAD files'}
        </Button>
      </div>
      <ResourceState state={versions} />
      {!versions.loading && !versions.error && !currentVersion && (
        <p className="section-description">No committed versions are available to download.</p>
      )}
      {isAssembly && (
        <p className="section-description">
          The package uses the selected root version and its recorded child revisions. Each
          child&apos;s current content version is pinned when you request the package and listed in
          its manifest.
        </p>
      )}
      {downloadError && (
        <p role="alert" className="error">
          {downloadError}
        </p>
      )}
      <p className="section-description">
        Artifacts displayed below:{' '}
        {snapshotId
          ? `historical version ${versions.data?.find((version) => version.id === snapshotId)?.sequence ?? ''}`
          : `current version ${currentVersion?.sequence ?? ''}`}
        . The package selector above only changes the ZIP download.
      </p>
      <ResourceState state={state} />
      {!state.loading &&
        !state.error &&
        state.data &&
        (rows.length ? (
          <div className="artifact-groups">
            {groups.map((group) => {
              const entries = rows.filter((row) => row.classification === group.key);
              return entries.length ? (
                <section key={group.key}>
                  <h3>{group.label}</h3>
                  <p className="section-description">{group.description}</p>
                  <DataTable
                    data={entries}
                    columns={columns}
                    defaultColumnVisibility={{
                      relationType: false,
                      source: false,
                      generator: false,
                      'derived-from': false,
                    }}
                    getRowId={(row) => row.id}
                  />
                </section>
              ) : null;
            })}
            {rows.some((row) => !groups.some((group) => group.key === row.classification)) && (
              <section>
                <h3>Other artifacts</h3>
                <DataTable
                  data={rows.filter(
                    (row) => !groups.some((group) => group.key === row.classification)
                  )}
                  columns={columns}
                  defaultColumnVisibility={{
                    relationType: false,
                    source: false,
                    generator: false,
                    'derived-from': false,
                  }}
                  getRowId={(row) => row.id}
                />
              </section>
            )}
          </div>
        ) : (
          <p className="empty-panel">No artifacts are available.</p>
        ))}
    </section>
  );
}
function readableName(key: string) {
  return key.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
}
function PropertyValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '')
    return <span className="muted">Not provided</span>;
  if (typeof value === 'object')
    return (
      <details>
        <summary>View details</summary>
        <pre className="property-json">{JSON.stringify(value, null, 2)}</pre>
      </details>
    );
  return <span>{String(value)}</span>;
}
function PropertyList({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values ?? {});
  return entries.length ? (
    <dl className="property-list">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{readableName(key)}</dt>
          <dd>
            <PropertyValue value={value} />
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <p className="empty-panel">No properties are available.</p>
  );
}
function MetadataSection({
  path,
  item,
  declared,
}: {
  path: string;
  item: Item;
  declared?: Record<string, unknown> | null;
}) {
  const state = useResource<RevisionMetadata>(path);
  return (
    <section id="properties" className="revision-properties-view">
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <>
          <p className="revision-metadata-status">
            Metadata: <strong>{readableName(state.data.metadata_status)}</strong> · Extraction:{' '}
            <strong>{readableName(state.data.current.status)}</strong>
          </p>
          {state.data.current.warnings?.length > 0 && (
            <details className="revision-diagnostics">
              <summary>Extraction warnings ({state.data.current.warnings.length})</summary>
              <pre className="property-json">
                {JSON.stringify(state.data.current.warnings, null, 2)}
              </pre>
            </details>
          )}
          {state.data.current.errors?.length > 0 && (
            <details className="revision-diagnostics" open>
              <summary>Extraction errors ({state.data.current.errors.length})</summary>
              <pre className="property-json">
                {JSON.stringify(state.data.current.errors, null, 2)}
              </pre>
            </details>
          )}
          {Object.keys(state.data.current.metadata ?? {}).length ? (
            <PropertiesSection item={item} metadata={state.data.current.metadata} />
          ) : (
            <>
              <SectionTitle
                title="Properties"
                description="CAD and derived metadata associated with this revision."
              />
              <p className="empty-panel">
                No extracted CAD properties are available for this revision.
              </p>
            </>
          )}
          <div className="revision-raw-properties">
            <details>
              <summary>Declared revision properties</summary>
              <PropertyList values={declared ?? {}} />
            </details>
            <details>
              <summary>Extracted metadata</summary>
              <PropertyList values={state.data.current.metadata ?? {}} />
            </details>
            <details>
              <summary>Derived properties</summary>
              <PropertyList values={state.data.current.derived ?? {}} />
            </details>
          </div>
        </>
      )}
      {state.error && (
        <div className="revision-raw-properties">
          <details>
            <summary>Declared revision properties</summary>
            <PropertyList values={declared ?? {}} />
          </details>
        </div>
      )}
    </section>
  );
}
function RevisionActionChoices({
  itemId,
  revisionId,
  revision,
  canEdit,
  canReview,
  isAdmin,
  closeMenu,
  refreshToken,
  activeSection,
  availability,
}: {
  itemId: string;
  revisionId: string;
  revision: Revision;
  canEdit: boolean;
  canReview: boolean;
  isAdmin: boolean;
  canRelease: boolean;
  closeMenu: () => void;
  refreshToken: number;
  activeSection: string;
  availability: RevisionActionAvailability | undefined;
}) {
  const itemPath = `/items/${encodeURIComponent(itemId)}`;
  const revisionPath = `${itemPath}/revisions/${encodeURIComponent(revisionId)}`;
  return (
    <>
      <Link
        role="menuitem"
        aria-current={activeSection === 'compare' ? 'page' : undefined}
        to={`${revisionPath}?section=compare&to=${encodeURIComponent(revisionId)}`}
        onClick={closeMenu}
      >
        Compare revisions
      </Link>
      {availability?.is_creator &&
      canEdit &&
      revision.lifecycle_state === 'WORKING' &&
      revision.checkout_status !== 'CHECKED_OUT_BY_OTHER' ? (
        <Link
          role="menuitem"
          aria-current={activeSection === 'edit' ? 'page' : undefined}
          to={`${revisionPath}?section=edit`}
          onClick={closeMenu}
        >
          Edit revision
        </Link>
      ) : null}
      {availability?.can_generate &&
        (availability.worker_configured ? (
          <Link
            role="menuitem"
            aria-current={activeSection === 'generate' ? 'page' : undefined}
            to={`${revisionPath}?section=generate`}
            onClick={closeMenu}
          >
            Generate metadata &amp; preview
          </Link>
        ) : (
          <button
            type="button"
            role="menuitem"
            disabled
            title="No matching CAD worker is configured"
          >
            Generate metadata &amp; preview
          </button>
        ))}
      {canReview && availability?.can_review ? (
        <Link
          role="menuitem"
          aria-current={activeSection === 'review' ? 'page' : undefined}
          to={`${revisionPath}?section=review`}
          onClick={closeMenu}
        >
          Review
        </Link>
      ) : null}
      <RevisionLifecycle
        key={`${revisionId}:${refreshToken}`}
        itemId={itemId}
        revision={revision}
        canEdit={canEdit}
        isAdmin={isAdmin}
        canRelease={!!availability?.can_release}
        isCreator={!!availability?.is_creator}
        closeMenu={closeMenu}
        activeSection={activeSection}
      />
    </>
  );
}
type RevisionActionAvailability = {
  is_creator: boolean;
  is_assigned_reviewer: boolean;
  can_review: boolean;
  can_release: boolean;
  can_generate: boolean;
  worker_configured: boolean;
};
export function ItemWorkspace({
  canEdit,
  canReview,
  isAdmin,
  canRelease,
}: {
  canEdit: boolean;
  canReview: boolean;
  isAdmin: boolean;
  canRelease: boolean;
}) {
  const { itemId = '', revisionId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [lifecycleRefreshToken, setLifecycleRefreshToken] = useState(0);
  const [selectedOccurrence, setSelectedOccurrence] = useState<{
    revisionId: string;
    id?: string;
  }>();
  const [viewerCommandState, setViewerCommandState] = useState<{
    revisionId: string;
    command: AssistantViewerCommand;
  }>();
  const onManagedSelection = useCallback(
    (id?: string) => {
      setSelectedOccurrence((current) =>
        current && current.revisionId === revisionId && current.id === id
          ? current
          : { revisionId: revisionId ?? '', id }
      );
    },
    [revisionId]
  );
  const itemPath = `/items/${encodeURIComponent(itemId)}`;
  const revisionPath = revisionId ? `${itemPath}/revisions/${encodeURIComponent(revisionId)}` : '';
  const actionAvailability = useResource<RevisionActionAvailability>(
    revisionId ? `${revisionPath}/action-availability` : null
  );
  const basePath = revisionPath || itemPath;
  const item = useResource<Item>(itemPath);
  const revisions = useResource<Revision[]>(`${itemPath}/revisions`);
  const revision = useResource<Revision>(revisionPath || null);
  const requestedVersionId = revisionId ? params.get('version') : null;
  const versionList = useResource<ContentSnapshot[]>(
    requestedVersionId && revisionId
      ? `/item-revisions/${encodeURIComponent(revisionId)}/content-snapshots`
      : null
  );
  const selectedVersion = versionList.data?.find((row) => row.id === requestedVersionId);
  const currentVersion = versionList.data?.reduce<ContentSnapshot | undefined>(
    (latest, row) => (!latest || row.sequence > latest.sequence ? row : latest),
    undefined
  );
  const versionReady =
    !requestedVersionId || (!versionList.loading && !versionList.error && !!selectedVersion);
  const historicalSnapshotId =
    selectedVersion && selectedVersion.id !== currentVersion?.id ? selectedVersion.id : undefined;
  const selectedOccurrenceId =
    selectedOccurrence && selectedOccurrence.revisionId === revisionId && !historicalSnapshotId
      ? selectedOccurrence.id
      : undefined;
  const viewerCommand =
    viewerCommandState && viewerCommandState.revisionId === revisionId && !historicalSnapshotId
      ? viewerCommandState.command
      : undefined;
  const sections = revisionId
    ? [
        { id: 'overview', label: 'Overview', group: 'Overview' },
        { id: 'properties', label: 'Properties', group: 'Content' },
        { id: 'versions', label: 'Versions', group: 'Content' },
        { id: 'preview', label: 'Preview', group: 'Content' },
        { id: 'revision-files', label: 'Artifacts', group: 'Content' },
        { id: 'revision-attachments', label: 'Attachments', group: 'Content' },
        { id: 'structure', label: 'Structure', group: 'Structure' },
        { id: 'relationships', label: 'Relationships', group: 'Structure' },
        ...(item.data?.item_type === 'ASSEMBLY'
          ? [{ id: 'intelligence', label: 'BOM Intelligence', group: 'Structure' }]
          : []),
        { id: 'stages', label: 'Stages', group: 'Activity' },
        { id: 'history', label: 'History', group: 'Activity' },
        { id: 'compare', label: 'Compare revisions', group: 'Actions' },
        { id: 'edit', label: 'Edit revision', group: 'Actions' },
        { id: 'generate', label: 'Generate metadata & preview', group: 'Actions' },
        { id: 'review', label: 'Review', group: 'Actions' },
        { id: 'approval', label: 'Request approval', group: 'Actions' },
        { id: 'release', label: 'Release revision', group: 'Actions' },
        { id: 'withdraw', label: 'Withdraw request', group: 'Actions' },
      ]
    : [
        { id: 'overview', label: 'Overview', group: 'Item' },
        { id: 'revisions', label: 'Revisions', group: 'Item' },
        { id: 'item-attachments', label: 'Item attachments', group: 'Item' },
        { id: 'history', label: 'History', group: 'Item' },
      ];
  const requestedSection = params.get('section') ?? 'overview';
  const activeSection = sections.some((section) => section.id === requestedSection)
    ? requestedSection
    : 'overview';
  useEffect(() => {
    setSelectedOccurrence(undefined);
    setViewerCommandState(undefined);
  }, [revisionId, historicalSnapshotId]);
  useEffect(() => {
    if (activeSection !== 'preview') setSelectedOccurrence(undefined);
  }, [activeSection]);
  const actionItem = item.data;
  const actionRevision = revision.data;
  const overviewRevision =
    revisionId && !revision.loading && !revision.error ? revision.data : undefined;
  return (
    <>
      <p className="breadcrumbs">
        <Link to="/library">Library</Link> /{' '}
        <Link to={itemPath}>{item.data?.item_number ?? 'Item'}</Link>
        {revisionId && ` / Revision ${revision.data?.revision_code ?? ''}`}
      </p>
      <ResourceState state={item} />
      {!item.loading && !item.error && item.data && (
        <>
          <div className="item-workspace-heading">
            <div>
              <h1>{item.data.name}</h1>
              <p className="intro">
                {item.data.item_number} ·{' '}
                {revisionId ? `Revision ${revision.data?.revision_code ?? ''}` : 'Item overview'}
              </p>
            </div>
            <label className="revision-selector">
              {revisionId ? 'Revision' : 'Open revision'}
              <select
                value={revisionId ?? ''}
                disabled={
                  revisions.loading ||
                  !!revisions.error ||
                  (!revisionId && revisions.data?.length === 0)
                }
                onChange={(event) =>
                  navigate(`${itemPath}/revisions/${encodeURIComponent(event.target.value)}`)
                }
              >
                <option value="" disabled>
                  {revisions.data?.length === 0 ? 'No revisions available' : 'Select revision'}
                </option>
                {revisions.data?.map((entry) => (
                  <option value={entry.id} key={entry.id}>
                    {entry.revision_code}
                  </option>
                ))}
                {revisionId && !revisions.data?.some((entry) => entry.id === revisionId) && (
                  <option value={revisionId}>
                    {revision.data?.revision_code ?? 'Current revision'}
                  </option>
                )}
              </select>
            </label>
          </div>
          {!revisionId && canEdit && (
            <ItemActions
              item={item.data}
              revisions={revisions.data ?? []}
              refresh={() => {
                item.retry();
                revisions.retry();
              }}
            />
          )}
          {revisionId ? (
            <RevisionIndexTabs
              sections={sections}
              activeSection={activeSection}
              basePath={basePath}
              versionId={requestedVersionId ?? undefined}
              actions={
                actionItem && actionRevision && !revision.error
                  ? (closeMenu) => (
                      <RevisionActionChoices
                        itemId={itemId}
                        revisionId={revisionId}
                        revision={actionRevision}
                        canEdit={canEdit}
                        canReview={canReview}
                        isAdmin={isAdmin}
                        canRelease={canRelease}
                        closeMenu={closeMenu}
                        refreshToken={lifecycleRefreshToken}
                        activeSection={activeSection}
                        availability={actionAvailability.data}
                      />
                    )
                  : undefined
              }
            />
          ) : (
            <nav className="section-navigation" aria-label="Item sections">
              <div className="section-nav">
                {sections.map((section) => (
                  <Link
                    key={section.id}
                    to={section.id === 'overview' ? basePath : `${basePath}?section=${section.id}`}
                    aria-current={activeSection === section.id ? 'page' : undefined}
                  >
                    {section.label}
                  </Link>
                ))}
              </div>
            </nav>
          )}
          {revisionId && <ResourceState state={revision} />}
          {requestedVersionId && <ResourceState state={versionList} />}
          {requestedVersionId && !versionList.loading && !versionList.error && !selectedVersion && (
            <p className="section-error" role="alert">
              This version does not belong to the selected revision.{' '}
              <Link to={basePath}>Open current revision</Link>
            </p>
          )}
          {selectedVersion && (
            <p className="version-context-banner" role="status">
              Viewing Version {selectedVersion.sequence}
              {historicalSnapshotId ? ' · Historical content' : ' · Current content'}. Lifecycle and
              actions apply to the current revision. <Link to={basePath}>Return to current</Link>
            </p>
          )}
          <div key={`${basePath}:${activeSection}:${historicalSnapshotId ?? 'current'}`}>
            {activeSection === 'history' && (
              <HistorySection key={basePath} path={`${basePath}/history`} />
            )}
            {activeSection === 'overview' && versionReady && (
              <section>
                <SectionTitle
                  title="Overview"
                  description={
                    revisionId
                      ? 'Revision identity and selected model image.'
                      : 'Stable Item identity and description.'
                  }
                />
                <div className={revisionId ? 'revision-overview-layout' : ''}>
                  {(!revisionId || overviewRevision) && (
                    <OverviewProperties item={item.data} revision={overviewRevision} />
                  )}
                  {revisionId && overviewRevision && (
                    <RevisionThumbnail
                      key={`${revisionId}:${historicalSnapshotId ?? 'current'}`}
                      revisionId={revisionId}
                      itemNumber={item.data.item_number}
                      revisionCode={overviewRevision.revision_code}
                      snapshotId={historicalSnapshotId}
                      previewPath={`${basePath}?${new URLSearchParams({ section: 'preview', ...(requestedVersionId ? { version: requestedVersionId } : {}) })}`}
                    />
                  )}
                </div>
              </section>
            )}
            {activeSection === 'revisions' && (
              <section>
                <SectionTitle
                  title="Revisions"
                  description="Open a specific revision to inspect its content and state."
                />
                <ResourceState state={revisions} />
                {!revisions.loading && !revisions.error && revisions.data && (
                  <RevisionTable itemPath={itemPath} revisions={revisions.data} />
                )}
              </section>
            )}
            {!revisionId && activeSection === 'item-attachments' && (
              <Attachments
                key={itemPath}
                path={`${itemPath}/attachments`}
                scope="Item"
                editUrl={canEdit ? `${itemPath}/attachments/edit` : undefined}
              />
            )}
            {revisionId && !revision.error && revision.data && (
              <>
                {activeSection === 'versions' && versionReady && (
                  <VersionHistory
                    key={revisionId}
                    revisionId={revisionId}
                    itemId={itemId}
                    isAssembly={item.data.item_type === 'ASSEMBLY'}
                    canRestore={
                      canEdit &&
                      revision.data.lifecycle_state === 'WORKING' &&
                      revision.data.checkout_status !== 'CHECKED_OUT_BY_OTHER'
                    }
                  />
                )}
                {activeSection === 'structure' && versionReady && (
                  <StructureSection
                    key={`${revisionId}:${historicalSnapshotId ?? 'current'}`}
                    item={item.data}
                    revisionId={revisionId}
                    snapshotId={historicalSnapshotId}
                  />
                )}
                {activeSection === 'relationships' && (
                  <RelationshipsSection key={revisionId} itemId={itemId} revisionId={revisionId} />
                )}
                {activeSection === 'preview' && versionReady && (
                  <PreviewSection
                    key={`${revisionId}:${historicalSnapshotId ?? 'current'}`}
                    revisionId={revisionId}
                    revisionCode={revision.data.revision_code}
                    item={item.data}
                    snapshotId={historicalSnapshotId}
                    onManagedSelection={onManagedSelection}
                    viewerCommand={viewerCommand}
                  />
                )}
                {activeSection === 'revision-files' && versionReady && (
                  <ArtifactSection
                    key={`${revisionId}:${historicalSnapshotId ?? 'current'}`}
                    path={`/item-revisions/${encodeURIComponent(revisionId)}/artifacts${historicalSnapshotId ? `?snapshot_id=${encodeURIComponent(historicalSnapshotId)}` : ''}`}
                    revisionId={revisionId}
                    itemNumber={item.data.item_number}
                    revisionCode={revision.data.revision_code}
                    isAssembly={item.data.item_type === 'ASSEMBLY'}
                    snapshotId={historicalSnapshotId}
                  />
                )}
                {activeSection === 'revision-attachments' && (
                  <Attachments
                    key={revisionId}
                    path={`${revisionPath}/attachments`}
                    scope="Revision"
                    editUrl={canEdit ? `${revisionPath}/attachments/edit` : undefined}
                  />
                )}
                {activeSection === 'stages' && (
                  <RevisionStages key={revisionId} revisionId={revisionId} isAdmin={isAdmin} />
                )}
                {activeSection === 'intelligence' && (
                  <BOMIntelligenceSection key={revisionId} revisionId={revisionId} />
                )}
                {activeSection === 'properties' && (
                  <MetadataSection
                    key={revisionId}
                    path={`/revisions/${encodeURIComponent(revisionId)}/metadata`}
                    item={item.data}
                    declared={revision.data.metadata_json}
                  />
                )}
                {activeSection === 'generate' &&
                  actionAvailability.data?.worker_configured &&
                  actionAvailability.data?.can_generate && (
                    <GeneratePreviewPage key={revisionId} embedded />
                  )}
                {activeSection === 'compare' && (
                  <Suspense fallback={<p role="status">Loading comparison…</p>}>
                    <Comparison key={revisionId} embedded />
                  </Suspense>
                )}
                {activeSection === 'edit' &&
                  (actionAvailability.data?.is_creator &&
                  canEdit &&
                  revision.data.lifecycle_state === 'WORKING' &&
                  revision.data.checkout_status !== 'CHECKED_OUT_BY_OTHER' ? (
                    <Suspense fallback={<p role="status">Loading revision editor…</p>}>
                      <CheckinWorkflow
                        key={revisionId}
                        embedded
                        editContext={{
                          itemId,
                          itemNumber: item.data.item_number,
                          itemName: item.data.name,
                          itemType: item.data.item_type,
                          revisionId,
                          revisionCode: revision.data.revision_code,
                        }}
                      />
                    </Suspense>
                  ) : (
                    <p className="empty-panel">
                      Editing is unavailable for this revision or your role.
                    </p>
                  ))}
                {activeSection === 'review' && actionAvailability.data?.can_review && (
                  <Suspense fallback={<p role="status">Loading reviews…</p>}>
                    <ReviewInbox
                      key={revisionId}
                      canReview={canReview}
                      embedded
                      selectedRevisionId={revisionId}
                      onChanged={() => {
                        revision.retry();
                        actionAvailability.retry();
                        setLifecycleRefreshToken((value) => value + 1);
                      }}
                    />
                  </Suspense>
                )}
                {(['approval', 'release', 'withdraw'] as const).includes(
                  activeSection as Exclude<RevisionLifecycleAction, 'emergency'>
                ) && (
                  <RevisionLifecyclePanel
                    key={`${revisionId}:${activeSection}`}
                    itemId={itemId}
                    revision={revision.data}
                    action={activeSection as RevisionLifecycleAction}
                    canEdit={canEdit}
                    isAdmin={isAdmin}
                    canRelease={!!actionAvailability.data?.can_release}
                    isCreator={!!actionAvailability.data?.is_creator}
                    onChanged={() => {
                      revision.retry();
                      actionAvailability.retry();
                      setLifecycleRefreshToken((value) => value + 1);
                    }}
                  />
                )}
              </>
            )}
          </div>
          {revisionId && !revision.error && revision.data && versionReady && (
            <RevisionAssistant
              key={`${revisionId}:${historicalSnapshotId ? 'historical' : 'current'}`}
              itemId={itemId}
              revisionId={revisionId}
              itemNumber={item.data.item_number}
              isAssembly={item.data.item_type === 'ASSEMBLY'}
              selectedOccurrenceId={selectedOccurrenceId}
              historical={!!historicalSnapshotId}
              onViewerAction={(command) => {
                setViewerCommandState({ revisionId, command });
                navigate(`${basePath}?section=preview`);
              }}
            />
          )}
        </>
      )}
    </>
  );
}
function RevisionStages({ revisionId, isAdmin }: { revisionId: string; isAdmin: boolean }) {
  const state = useResource<StageJob[]>(
    `/revisions/${encodeURIComponent(revisionId)}/processing-jobs`
  );
  return (
    <>
      <ResourceState state={state} />
      {state.data && !state.error && (
        <div className="stages-page">
          <StagesPanel
            revisionId={revisionId}
            isAdmin={isAdmin}
            processingJobs={state.data}
            onProcessingChanged={async () => state.retry()}
          />
        </div>
      )}
    </>
  );
}
type HistoryEntry = {
  id: string;
  action: string;
  changed_by: string;
  occurred_at: string;
  details: Record<string, unknown>;
};
function HistorySection({ path }: { path: string }) {
  const state = useResource<HistoryEntry[]>(path);
  const columns: ColumnDef<HistoryEntry>[] = [
    {
      accessorKey: 'occurred_at',
      header: 'Date',
      cell: ({ getValue }) => new Date(String(getValue())).toLocaleString(),
    },
    {
      accessorKey: 'action',
      header: 'Action',
      cell: ({ getValue }) => String(getValue()).replaceAll('.', ' '),
    },
    { accessorKey: 'changed_by', header: 'Changed by' },
    {
      id: 'details',
      header: 'Details',
      cell: ({ row }) => <PropertyValue value={row.original.details} />,
    },
  ];
  return (
    <section>
      <SectionTitle title="History" description="Recorded changes and actions." />
      <ResourceState state={state} />
      {!state.loading && !state.error && state.data && (
        <DataTable
          data={state.data}
          columns={columns}
          getRowId={(row) => row.id}
          emptyMessage="No history available."
        />
      )}
    </section>
  );
}
function RevisionTable({ itemPath, revisions }: { itemPath: string; revisions: Revision[] }) {
  const columns: ColumnDef<Revision>[] = [
    {
      id: 'revision',
      header: 'Revision',
      accessorKey: 'revision_code',
      cell: ({ row }) => (
        <Link to={`${itemPath}/revisions/${encodeURIComponent(row.original.id)}`}>
          {row.original.revision_code}
        </Link>
      ),
    },
    { accessorKey: 'lifecycle_state', header: 'Lifecycle' },
    { accessorKey: 'state', header: 'Processing state' },
    { accessorKey: 'checkout_status', header: 'Checkout' },
    { accessorKey: 'cad_system', header: 'CAD system' },
  ];
  return (
    <DataTable
      data={revisions}
      columns={columns}
      getRowId={(row) => row.id}
      emptyMessage="This item has no revisions."
    />
  );
}
type ContentSnapshot = {
  id: string;
  sequence: number;
  commit_id: string;
  comment: string | null;
  created_at: string;
  artifact_count: number;
};
