import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  api,
  authorizationHeader,
  serverApiUrl,
  type ComparisonChange,
  type ComparisonDomain,
  type ComparisonExplanation,
  type Item,
  type RevisionComparison,
} from '../workflow-api';
import './Comparison.css';

type Revision = { id: string; revision_code: string; state: string; created_at: string };
type WhereUsedPathSegment = { occurrence_id: string; occurrence_key: string; quantity: number };
type ImpactCandidate = {
  occurrence_id: string;
  occurrence_key: string;
  parent_item: Item;
  parent_revision: Revision;
  direct: boolean;
  depth: number;
  total_quantity: number;
  path: WhereUsedPathSegment[];
};
type WhereUsed = { revision_id: string; items: ImpactCandidate[]; warnings: string[] };
type ComparisonMode = 'from' | 'to' | 'overlay';

const requestedDomains = [
  'METADATA',
  'PHYSICAL_PROPERTIES',
  'FILES',
  'ATTACHMENTS',
  'VALIDATION',
  'GEOMETRY',
  'FEATURES',
  'BOM',
  'DRAWING',
];
const domainLabels: Record<string, string> = {
  METADATA: 'Metadata',
  PHYSICAL_PROPERTIES: 'Physical properties',
  FILES: 'CAD files',
  ATTACHMENTS: 'Attachments',
  VALIDATION: 'Validation',
  GEOMETRY: 'Geometry',
  FEATURES: 'Features',
  BOM: 'BOM structure',
  DRAWING: 'Drawings',
};
const userFileKinds = new Set(['NATIVE', 'STEP', 'DRAWING', 'PDF']);
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const displayValue = (value: unknown): string => {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(displayValue).join(', ') || '—';
  if (isRecord(value))
    return Object.entries(value)
      .map(([key, entry]) => `${key}: ${displayValue(entry)}`)
      .join('; ');
  return String(value);
};
const friendlyPath = (path: string) =>
  path
    .replace(/^(metadata|physical_properties|features|geometry)\./i, '')
    .split('.')
    .map((part) => part.replaceAll('_', ' '))
    .join(' › ');
type VisibleChange = ComparisonChange & { display_path: string };

function objectDifferences(change: ComparisonChange): VisibleChange[] {
  if (
    !isRecord(change.old_value) &&
    !isRecord(change.new_value) &&
    !Array.isArray(change.old_value) &&
    !Array.isArray(change.new_value)
  )
    return [{ ...change, display_path: friendlyPath(change.property_path) }];
  const rows: VisibleChange[] = [];
  const walk = (before: unknown, after: unknown, suffix: string) => {
    if (isRecord(before) || isRecord(after)) {
      const left = isRecord(before) ? before : {};
      const right = isRecord(after) ? after : {};
      for (const key of [...new Set([...Object.keys(left), ...Object.keys(right)])].sort())
        walk(left[key], right[key], suffix ? `${suffix}.${key}` : key);
      return;
    }
    if (Array.isArray(before) || Array.isArray(after)) {
      const left = Array.isArray(before) ? before : [];
      const right = Array.isArray(after) ? after : [];
      for (let index = 0; index < Math.max(left.length, right.length); index += 1)
        walk(left[index], right[index], `${suffix}[${index + 1}]`);
      return;
    }
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    rows.push({
      ...change,
      id: `${change.id}:${suffix}`,
      change_type: before === undefined ? 'ADDED' : after === undefined ? 'REMOVED' : 'MODIFIED',
      old_value: before,
      new_value: after,
      display_path: friendlyPath(`${change.property_path}.${suffix}`),
    });
  };
  walk(change.old_value, change.new_value, '');
  return rows.length ? rows : [{ ...change, display_path: friendlyPath(change.property_path) }];
}

function visibleChanges(domain: ComparisonDomain): VisibleChange[] {
  if (domain.domain === 'FILES')
    return domain.changes
      .filter((change) => userFileKinds.has(String(change.metadata.kind ?? '').toUpperCase()))
      .map((change) => ({ ...change, display_path: friendlyPath(change.property_path) }));
  if (domain.domain === 'BOM')
    return domain.changes.map((change) => ({
      ...change,
      display_path: friendlyPath(change.property_path),
    }));
  return domain.changes.flatMap(objectDifferences);
}

export function bomOccurrenceIds(changes: ComparisonChange[]): string[] {
  const keys = [
    'from_occurrence_key',
    'to_occurrence_key',
    'from_native_occurrence_key',
    'to_native_occurrence_key',
    'occurrence_key',
  ];
  return [
    ...new Set(
      changes
        .flatMap((change) => keys.map((key) => change.metadata[key]))
        .filter((value): value is string => typeof value === 'string' && value.length > 0)
    ),
  ];
}

export function ComparisonPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { itemId = '', revisionId = '' } = useParams();
  return (
    <ComparisonWorkspace key={`${itemId}:${embedded ? revisionId : ''}`} embedded={embedded} />
  );
}

function ComparisonWorkspace({ embedded }: { embedded: boolean }) {
  const { itemId, revisionId: selectedRevisionId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const comparisonRequest = useRef(0);
  const [item, setItem] = useState<Item>();
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [fromId, setFromId] = useState(searchParams.get('from') ?? '');
  const [toId, setToId] = useState(
    searchParams.get('to') ?? (embedded ? (selectedRevisionId ?? '') : '')
  );
  const [comparison, setComparison] = useState<RevisionComparison>();
  const [selectedSection, setSelectedSection] = useState('OVERVIEW');
  const [selectedBomChanges, setSelectedBomChanges] = useState<ComparisonChange[]>();
  const [explanation, setExplanation] = useState<ComparisonExplanation>();
  const [impacts, setImpacts] = useState<ImpactCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const queryFrom = searchParams.get('from') ?? '';
  const queryTo = searchParams.get('to') ?? '';
  const lastQuery = useRef({ from: queryFrom, to: queryTo });

  useEffect(
    () => () => {
      comparisonRequest.current += 1;
    },
    []
  );
  useEffect(() => {
    if (lastQuery.current.from === queryFrom && lastQuery.current.to === queryTo) return;
    lastQuery.current = { from: queryFrom, to: queryTo };
    if (queryFrom === fromId && queryTo === toId) return;
    comparisonRequest.current += 1;
    setComparison(undefined);
    setExplanation(undefined);
    setImpacts([]);
    const target = queryTo || (embedded ? selectedRevisionId : revisions[0]?.id) || '';
    const targetIndex = revisions.findIndex((row) => row.id === target);
    setFromId(queryFrom || revisions[targetIndex + 1]?.id || '');
    setToId(target);
    setLoading(false);
  }, [queryFrom, queryTo, fromId, toId, embedded, selectedRevisionId, revisions]);

  useEffect(() => {
    if (!itemId) return;
    let live = true;
    setLoading(true);
    Promise.all([api<Item>(`/items/${itemId}`), api<Revision[]>(`/items/${itemId}/revisions`)])
      .then(([loadedItem, loadedRevisions]) => {
        if (!live) return;
        setItem(loadedItem);
        setRevisions(loadedRevisions);
        const targetId = toId || loadedRevisions[0]?.id || '';
        const targetIndex = loadedRevisions.findIndex((row) => row.id === targetId);
        if (!toId && targetId) setToId(targetId);
        if (!fromId)
          setFromId(loadedRevisions[targetIndex + 1]?.id ?? loadedRevisions[1]?.id ?? '');
      })
      .catch((reason) => {
        if (live) setError(reason instanceof Error ? reason.message : 'Unable to load revisions');
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [itemId]);

  const runComparison = async () => {
    if (!itemId || !fromId || !toId || fromId === toId) return;
    const requestId = ++comparisonRequest.current;
    setLoading(true);
    setError('');
    setExplanation(undefined);
    setSelectedBomChanges(undefined);
    setSelectedSection('OVERVIEW');
    try {
      const result = await api<RevisionComparison>(
        `/items/${itemId}/revision-comparisons`,
        'POST',
        { from_revision_id: fromId, to_revision_id: toId, domains: requestedDomains }
      );
      if (requestId !== comparisonRequest.current) return;
      setComparison(result);
      navigate(
        embedded && selectedRevisionId
          ? `/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(selectedRevisionId)}?${new URLSearchParams({ section: 'compare', from: fromId, to: toId })}`
          : `/items/${encodeURIComponent(itemId)}/compare?${new URLSearchParams({ from: fromId, to: toId })}`,
        { replace: true }
      );
      api<WhereUsed>(`/items/${itemId}/revisions/${toId}/where-used`)
        .then((response) => {
          if (requestId === comparisonRequest.current) setImpacts(response.items);
        })
        .catch(() => {
          if (requestId === comparisonRequest.current) setImpacts([]);
        });
    } catch (reason) {
      if (requestId === comparisonRequest.current)
        setError(reason instanceof Error ? reason.message : 'Unable to compare revisions');
    } finally {
      if (requestId === comparisonRequest.current) setLoading(false);
    }
  };
  useEffect(() => {
    if (fromId && toId && fromId !== toId && !comparison) void runComparison();
  }, [fromId, toId]);
  const requestExplanation = async (level: ComparisonExplanation['level']) => {
    if (!comparison) return;
    const requestId = comparisonRequest.current;
    try {
      const result = await api<ComparisonExplanation>(
        `/revision-comparisons/${comparison.id}/explanations`,
        'POST',
        { level }
      );
      if (requestId === comparisonRequest.current) setExplanation(result);
    } catch (reason) {
      if (requestId === comparisonRequest.current)
        setError(reason instanceof Error ? reason.message : 'Unable to explain comparison');
    }
  };
  const changed = comparison?.domains.flatMap(visibleChanges) ?? [];
  const selectedDomain = comparison?.domains.find((domain) => domain.domain === selectedSection);
  const majorCount = changed.filter((row) =>
    ['MAJOR', 'CRITICAL'].includes(row.significance)
  ).length;

  if (loading && !item)
    return (
      <section className="page">
        <p>Loading comparison…</p>
      </section>
    );
  return (
    <section className={`page comparison-page${embedded ? ' comparison-page--embedded' : ''}`}>
      {!embedded && (
        <div className="comparison-breadcrumb">
          <Link to={`/items/${itemId}`}>{item?.item_number ?? 'Item'}</Link>
          <span>/</span>
          <span>Compare revisions</span>
        </div>
      )}
      <header className="comparison-heading">
        <div>
          <p className="eyebrow">Revision comparison</p>
          {embedded ? <h2>Compare revisions</h2> : <h1>{item?.name ?? 'Comparison'}</h1>}
          <p className="page-subtitle">Review only the engineering information that changed</p>
        </div>
        {comparison && (
          <span className={`comparison-status status-${comparison.status.toLowerCase()}`}>
            {comparison.status}
          </span>
        )}
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <section className="comparison-selector" aria-label="Revision direction">
        <label>
          From revision
          <select
            value={fromId}
            onChange={(event) => {
              comparisonRequest.current += 1;
              setLoading(false);
              setExplanation(undefined);
              setImpacts([]);
              setComparison(undefined);
              setFromId(event.target.value);
            }}
          >
            {revisions.map((row) => (
              <option key={row.id} value={row.id}>
                {row.revision_code} — {row.state}
              </option>
            ))}
          </select>
        </label>
        <span className="comparison-arrow" aria-hidden="true">
          →
        </span>
        <label>
          To revision
          <select
            value={toId}
            onChange={(event) => {
              comparisonRequest.current += 1;
              setLoading(false);
              setExplanation(undefined);
              setImpacts([]);
              setComparison(undefined);
              setToId(event.target.value);
            }}
          >
            {revisions.map((row) => (
              <option key={row.id} value={row.id}>
                {row.revision_code} — {row.state}
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary-button"
          disabled={!fromId || !toId || fromId === toId || loading}
          onClick={() => void runComparison()}
        >
          {loading ? 'Comparing…' : 'Compare'}
        </button>
      </section>
      {comparison && (
        <div className="comparison-workspace">
          <nav className="comparison-tree" aria-label="Comparison categories">
            <TreeButton
              label="Overview"
              status={comparison.status}
              selected={selectedSection === 'OVERVIEW'}
              onClick={() => setSelectedSection('OVERVIEW')}
            />
            <TreeButton
              label="3D comparison"
              selected={selectedSection === 'THREE_D'}
              onClick={() => setSelectedSection('THREE_D')}
              deferred
            />
            <div className="comparison-tree-group">
              <span>Differences</span>
              {comparison.domains.map((domain) => (
                <TreeButton
                  key={domain.id}
                  label={domainLabels[domain.domain] ?? domain.domain.replaceAll('_', ' ')}
                  count={visibleChanges(domain).length}
                  status={domain.status}
                  selected={selectedSection === domain.domain}
                  onClick={() => setSelectedSection(domain.domain)}
                />
              ))}
            </div>
          </nav>
          <div className="comparison-content">
            {selectedSection === 'OVERVIEW' && (
              <ComparisonOverview
                comparison={comparison}
                changes={changed}
                majorCount={majorCount}
                impacts={impacts}
                explanation={explanation}
                requestExplanation={requestExplanation}
              />
            )}
            {selectedSection === 'THREE_D' && (
              <ComparisonViewer
                fromId={fromId}
                toId={toId}
                bomChanges={
                  selectedBomChanges ?? changed.filter((row) => row.categories.includes('BOM'))
                }
              />
            )}
            {selectedDomain && (
              <DomainPanel
                domain={selectedDomain}
                onSelectChange={
                  selectedDomain.domain === 'BOM'
                    ? (change) => {
                        setSelectedBomChanges([change]);
                        setSelectedSection('THREE_D');
                      }
                    : undefined
                }
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function TreeButton({
  label,
  count,
  status,
  selected,
  deferred,
  onClick,
}: {
  label: string;
  count?: number;
  status?: string;
  selected: boolean;
  deferred?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`comparison-tree-node${selected ? ' selected' : ''}`}
      onClick={onClick}
      aria-current={selected ? 'page' : undefined}
    >
      <span>
        {label}
        {deferred && <small>Loads on demand</small>}
      </span>
      {count !== undefined ? (
        <strong>{count}</strong>
      ) : (
        status && <i className={`tree-status domain-${status.toLowerCase()}`} title={status} />
      )}
    </button>
  );
}

function ComparisonOverview({
  comparison,
  changes,
  majorCount,
  impacts,
  explanation,
  requestExplanation,
}: {
  comparison: RevisionComparison;
  changes: VisibleChange[];
  majorCount: number;
  impacts: ImpactCandidate[];
  explanation?: ComparisonExplanation;
  requestExplanation: (level: ComparisonExplanation['level']) => Promise<void>;
}) {
  return (
    <>
      <section className="comparison-summary" aria-label="Comparison summary">
        <article>
          <strong>{changes.length}</strong>
          <span>User-visible changes</span>
        </article>
        <article>
          <strong>{majorCount}</strong>
          <span>Major or critical</span>
        </article>
        <article>
          <strong>
            {
              comparison.domains.filter(
                (row) => visibleChanges(row).length === 0 && row.status === 'UNCHANGED'
              ).length
            }
          </strong>
          <span>Unchanged categories</span>
        </article>
        <article>
          <strong>
            {
              comparison.domains.filter((row) => ['UNSUPPORTED', 'FAILED'].includes(row.status))
                .length
            }
          </strong>
          <span>Unavailable categories</span>
        </article>
      </section>
      <section className="comparison-review">
        <div className="comparison-review-heading">
          <div>
            <h2>Review intelligence</h2>
            <p>Select a category on the left for its detailed differences.</p>
          </div>
          <div className="explanation-actions">
            <button onClick={() => void requestExplanation('QUICK')}>Quick summary</button>
            <button onClick={() => void requestExplanation('ENGINEERING')}>
              Engineering summary
            </button>
            <button onClick={() => void requestExplanation('DETAILED')}>Detailed summary</button>
          </div>
        </div>
        <ReviewChecklist changes={changes} />
        {impacts.length > 0 && (
          <div className="impact-hints">
            <h3>Potential downstream impact</h3>
            <p>
              These assemblies use the target revision directly or through nested subassemblies.
              Compatibility impact is not proven.
            </p>
            <ul>
              {impacts.map((impact) => (
                <li
                  key={
                    impact.path.map((row) => row.occurrence_id).join('/') || impact.occurrence_id
                  }
                >
                  <Link
                    to={`/items/${impact.parent_item.id}/revisions/${impact.parent_revision.id}`}
                  >
                    {impact.parent_item.item_number} revision {impact.parent_revision.revision_code}
                  </Link>{' '}
                  — {impact.direct ? 'direct' : `indirect · ${impact.depth} levels`} · quantity{' '}
                  {impact.total_quantity}
                  <small>
                    {impact.path.map((row) => row.occurrence_key).join(' › ') ||
                      impact.occurrence_key}
                  </small>
                </li>
              ))}
            </ul>
          </div>
        )}
        {explanation && (
          <article className="comparison-explanation">
            <div>
              <strong>{explanation.level}</strong> · {explanation.mode}
            </div>
            <p>{explanation.summary}</p>
            <ul>
              {explanation.facts.map((fact) => (
                <li key={fact.change_keys.join('|')}>{fact.text}</li>
              ))}
            </ul>
            <p>{explanation.cause.text}</p>
            {explanation.advisory.map((line) => (
              <p key={line}>{line}</p>
            ))}
            {explanation.warnings.map((line) => (
              <p className="domain-warning" key={line}>
                {line}
              </p>
            ))}
          </article>
        )}
      </section>
    </>
  );
}

function DomainPanel({
  domain,
  onSelectChange,
}: {
  domain: ComparisonDomain;
  onSelectChange?: (change: ComparisonChange) => void;
}) {
  const changes = visibleChanges(domain);
  const internalFileCount = domain.domain === 'FILES' ? domain.changes.length - changes.length : 0;
  return (
    <section className="comparison-domain-panel">
      <header>
        <div>
          <p className="eyebrow">Difference category</p>
          <h2>{domainLabels[domain.domain] ?? domain.domain.replaceAll('_', ' ')}</h2>
        </div>
        <span className={`domain-status domain-${domain.status.toLowerCase()}`}>
          {domain.status}
        </span>
      </header>
      {domain.domain === 'FILES' && internalFileCount > 0 && (
        <p className="comparison-note">
          {internalFileCount} generated preview or internal data file{' '}
          {internalFileCount === 1 ? 'change is' : 'changes are'} hidden.
        </p>
      )}
      {domain.warnings.map((warning) => (
        <p className="domain-warning" key={warning}>
          {warning}
        </p>
      ))}
      {domain.error_detail && <p className="error">{domain.error_detail}</p>}
      {!changes.length ? (
        <div className="comparison-empty">
          <strong>No user-visible differences</strong>
          <p>
            {domain.status === 'UNSUPPORTED'
              ? 'This information is unavailable for one or both revisions.'
              : internalFileCount > 0
                ? 'No CAD source or drawing files changed; generated internal files are hidden.'
                : 'The compared values are the same.'}
          </p>
        </div>
      ) : domain.domain === 'FILES' ? (
        <FileChanges changes={changes} />
      ) : (
        <DifferenceTable changes={changes} onSelectChange={onSelectChange} />
      )}
    </section>
  );
}

function DifferenceTable({
  changes,
  onSelectChange,
}: {
  changes: VisibleChange[];
  onSelectChange?: (change: ComparisonChange) => void;
}) {
  return (
    <div className="comparison-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Change</th>
            <th>Property</th>
            <th>Before</th>
            <th>After</th>
            <th>Impact</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={change.id} className={change.confidence < 0.8 ? 'uncertain' : ''}>
              <td>
                <span className={`change-badge change-${change.change_type.toLowerCase()}`}>
                  {change.change_type}
                </span>
              </td>
              <td>
                {onSelectChange ? (
                  <button
                    className="comparison-change-link"
                    onClick={() => onSelectChange(change)}
                    aria-label={`Show ${change.change_type.toLowerCase()} ${change.display_path} in 3D`}
                  >
                    {change.display_path}
                  </button>
                ) : (
                  change.display_path
                )}
              </td>
              <td>{displayValue(change.old_value)}</td>
              <td>
                {displayValue(change.new_value)}
                {change.unit ? ` ${change.unit}` : ''}
              </td>
              <td>
                {change.significance}
                {change.confidence < 1 ? (
                  <small>{Math.round(change.confidence * 100)}% confidence</small>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FileChanges({ changes }: { changes: VisibleChange[] }) {
  return (
    <div className="file-change-list">
      {changes.map((change) => {
        const oldValue = isRecord(change.old_value) ? change.old_value : {};
        const newValue = isRecord(change.new_value) ? change.new_value : {};
        const size = Number(newValue.byte_size ?? oldValue.byte_size ?? 0);
        return (
          <article key={change.id}>
            <span className={`change-badge change-${change.change_type.toLowerCase()}`}>
              {change.change_type}
            </span>
            <div>
              <strong>{String(change.metadata.filename ?? change.display_path)}</strong>
              <small>
                {String(change.metadata.kind ?? 'CAD file')} {size ? `· ${formatBytes(size)}` : ''}
              </small>
            </div>
          </article>
        );
      })}
    </div>
  );
}
const formatBytes = (bytes: number) =>
  bytes >= 1_000_000
    ? `${(bytes / 1_000_000).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1_000))} KB`;

function ReviewChecklist({ changes }: { changes: ComparisonChange[] }) {
  const items = useMemo(() => {
    const result = new Set<string>();
    if (changes.some((row) => row.categories.includes('MATERIAL')))
      result.add('Advisory: confirm material and supplier requirements.');
    if (changes.some((row) => row.categories.includes('DIMENSIONAL')))
      result.add('Advisory: verify mating interfaces and manufacturing capability.');
    if (changes.some((row) => row.categories.includes('BOM')))
      result.add('Advisory: review assembly substitutions, quantity, and placement.');
    if (changes.some((row) => row.categories.includes('DRAWING')))
      result.add('Advisory: review drawing notes, dimensions, and tolerances.');
    if (changes.some((row) => row.confidence < 0.8))
      result.add('Advisory: manually resolve low-confidence matches.');
    return [...result];
  }, [changes]);
  return (
    <div>
      <h3>Suggested checklist</h3>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">No rule-based review suggestions were triggered.</p>
      )}
    </div>
  );
}

export function ComparisonViewer({
  fromId,
  toId,
  bomChanges,
  fromSnapshotId,
  toSnapshotId,
}: {
  fromId: string;
  toId: string;
  bomChanges: ComparisonChange[];
  fromSnapshotId?: string;
  toSnapshotId?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<import('../viewer').ModelryxViewer | undefined>(undefined);
  const [mode, setMode] = useState<ComparisonMode>('overlay');
  const [message, setMessage] = useState('Loading 3D comparison…');
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const { ModelryxViewer } = await import('../viewer');
        const viewer = new ModelryxViewer(container);
        viewerRef.current = viewer;
        const fetchPreview = async (revisionId: string, snapshotId?: string) => {
          for (const suffix of snapshotId
            ? [`preview/model?snapshot_id=${encodeURIComponent(snapshotId)}`]
            : ['preview', 'preview/model']) {
            const response = await fetch(serverApiUrl(`/revisions/${revisionId}/${suffix}`), {
              headers: authorizationHeader(),
              signal: controller.signal,
            });
            if (response.ok) {
              const blob = await response.blob();
              return suffix === 'preview'
                ? new File([blob], `revision-${revisionId}.pfg`, { type: blob.type })
                : blob;
            }
          }
          throw new Error('A 3D preview is unavailable for one or both selected versions');
        };
        const [from, to] = await Promise.all([
          fetchPreview(fromId, fromSnapshotId),
          fetchPreview(toId, toSnapshotId),
        ]);
        await viewer.loadComparison(from, to, { signal: controller.signal });
        setMessage('');
      } catch (reason) {
        if (!controller.signal.aborted)
          setMessage(reason instanceof Error ? reason.message : 'Unable to load 3D comparison');
      }
    };
    void load();
    return () => {
      controller.abort();
      viewerRef.current?.dispose();
      viewerRef.current = undefined;
    };
  }, [fromId, toId, fromSnapshotId, toSnapshotId]);
  const changeMode = (next: ComparisonMode) => {
    setMode(next);
    viewerRef.current?.setComparisonMode(next);
  };
  const affected = bomOccurrenceIds(bomChanges);
  return (
    <section className="comparison-viewer-panel">
      <div className="comparison-viewer-toolbar">
        <div>
          <p className="eyebrow">Loaded on demand</p>
          <h2>3D comparison</h2>
        </div>
        <div role="group" aria-label="3D comparison mode">
          {(['from', 'overlay', 'to'] as ComparisonMode[]).map((value) => (
            <button
              className={mode === value ? 'active' : ''}
              key={value}
              onClick={() => changeMode(value)}
            >
              {value === 'from' ? 'Before' : value === 'to' ? 'After' : 'Overlay'}
            </button>
          ))}
        </div>
        {affected.length > 0 && (
          <button onClick={() => viewerRef.current?.selectOccurrences(affected)}>
            Highlight changed BOM occurrences
          </button>
        )}
      </div>
      <div className="comparison-viewer">
        <div ref={containerRef} className="comparison-viewer-canvas" />
        {message && <div className="comparison-viewer-message">{message}</div>}
      </div>
      <p className="comparison-legend">
        <span className="removed-swatch" />
        Before {fromSnapshotId ? 'version' : 'revision'} <span className="added-swatch" />
        After {toSnapshotId ? 'version' : 'revision'}
      </p>
    </section>
  );
}
