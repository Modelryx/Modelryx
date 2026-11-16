import { useEffect, useState } from 'react';
import { api } from '../../workflow-api';
import type { StagingResult } from './model';

type Occurrence = {
  occurrence_id: string;
  parent_occurrence_id?: string | null;
  name: string;
  part_number?: string | null;
  revision?: string | null;
  quantity: number;
  dependency_document?: string | null;
  dependency_status?: string | null;
  suppressed?: boolean;
  visible?: boolean;
};
type Candidate = {
  id: string;
  item_number: string;
  name: string;
  item_type: string;
  current_revision_id: string | null;
  current_revision_code: string | null;
};

export function NativeBomReview({
  staging,
  disabled,
  onUpdate,
}: {
  staging: StagingResult;
  disabled: boolean;
  onUpdate: (stage: StagingResult) => void;
}) {
  const [occurrences, setOccurrences] = useState<Occurrence[]>([]);
  const [subassemblies, setSubassemblies] = useState<Set<string>>(new Set());
  const [transforms, setTransforms] = useState<Record<string, number[]>>({});
  const [search, setSearch] = useState('');
  const [matches, setMatches] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const revisionId = staging.base_revision_id;
  useEffect(() => {
    if (!revisionId) return;
    let active = true;
    api<{
      current: {
        metadata: {
          assembly?: { components?: Occurrence[] };
          native?: {
            transform_convention?: string;
            occurrences?: Array<{ id?: string; transform?: unknown }>;
          };
        };
      };
    }>(`/revisions/${revisionId}/metadata`)
      .then((result) => {
        if (active) {
          const components = result.current.metadata.assembly?.components ?? [];
          setOccurrences(components.filter((component) => !component.parent_occurrence_id));
          setSubassemblies(
            new Set(
              components.flatMap((component) =>
                component.parent_occurrence_id ? [component.parent_occurrence_id] : []
              )
            )
          );
          const native = result.current.metadata.native;
          const resolved: Record<string, number[]> = {};
          const duplicates = new Set<string>();
          if (
            native?.transform_convention === 'parent-row-major-mm' &&
            Array.isArray(native.occurrences)
          ) {
            for (const occurrence of native.occurrences) {
              const matrix = occurrence.transform;
              if (
                occurrence.id &&
                Array.isArray(matrix) &&
                matrix.length === 16 &&
                matrix.every((value) => typeof value === 'number' && Number.isFinite(value)) &&
                matrix[12] === 0 &&
                matrix[13] === 0 &&
                matrix[14] === 0 &&
                matrix[15] === 1
              ) {
                if (resolved[occurrence.id] || duplicates.has(occurrence.id)) {
                  delete resolved[occurrence.id];
                  duplicates.add(occurrence.id);
                } else resolved[occurrence.id] = matrix;
              }
            }
          }
          setTransforms(resolved);
        }
      })
      .catch(() => {
        if (active) setOccurrences([]);
      });
    return () => {
      active = false;
    };
  }, [revisionId]);
  if (!revisionId || !occurrences.length || staging.proposal?.item?.item_type !== 'ASSEMBLY')
    return null;
  const bom = (staging.proposal?.bom ?? []) as Array<{
    instance_name: string;
    child_revision_id?: string;
  }>;
  const linked = new Set(bom.map((line) => line.instance_name.toLowerCase()));
  const add = async (component: Occurrence, childRevision: string) => {
    if (
      !childRevision ||
      !component.occurrence_id ||
      !Number.isInteger(component.quantity) ||
      component.quantity < 1 ||
      linked.has(component.occurrence_id.toLowerCase())
    )
      return;
    setBusy(true);
    setError('');
    try {
      const updated = await api<StagingResult>(`/staging/${staging.id}/proposal`, 'PUT', {
        ...staging.proposal,
        expected_snapshot_version: staging.snapshot_version,
        bom: [
          ...bom,
          {
            parent_revision_id: revisionId,
            child_revision_id: childRevision,
            instance_name: component.occurrence_id,
            quantity: component.quantity,
            source_reference: component.dependency_document ?? '',
            ...(transforms[component.occurrence_id]
              ? { transform: transforms[component.occurrence_id] }
              : {}),
            is_suppressed: component.suppressed ?? false,
            is_visible: component.visible ?? true,
            properties: {
              native_placement: transforms[component.occurrence_id] ? 'reported' : 'unavailable',
              native_component_name: component.name,
            },
          },
        ],
      });
      onUpdate(updated);
      setSelected('');
      setMatches([]);
      setSearch('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to stage the BOM relationship.');
    } finally {
      setBusy(false);
    }
  };
  const find = async () => {
    setBusy(true);
    setError('');
    try {
      const escaped = search.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      if (!escaped) throw new Error('Enter an item number.');
      const result = await api<{ items: Candidate[] }>(
        `/items?limit=50&item_number_regex=${encodeURIComponent(escaped)}`
      );
      setMatches(
        result.items.filter(
          (item) =>
            (!subassemblies.has(selected) || item.item_type === 'ASSEMBLY') &&
            item.current_revision_id &&
            item.current_revision_id !== revisionId
        )
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to search items.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="item-revision-panel">
      <h2>Resolve native components</h2>
      <p className="muted">
        Choose an existing Item revision for each direct occurrence. Resolve nested occurrences in
        their own subassemblies. Declared placements are preserved; where placement is unavailable
        the staged relationship uses an identity transform.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {occurrences.map((component, index) => (
        <div key={`${component.occurrence_id}-${index}`}>
          <strong>{component.name}</strong> · {component.occurrence_id}
          {!transforms[component.occurrence_id] && <span> · Placement unavailable</span>}
          {component.dependency_document && <span> · {component.dependency_document}</span>}
          {linked.has(component.occurrence_id.toLowerCase()) ? (
            <span> · Staged in BOM</span>
          ) : !Number.isInteger(component.quantity) || component.quantity < 1 ? (
            <span> · Invalid native quantity; correct the source before resolving.</span>
          ) : (
            <>
              <button
                type="button"
                disabled={disabled || busy}
                onClick={() => {
                  setSelected(component.occurrence_id);
                  setSearch(component.part_number ?? '');
                  setMatches([]);
                  setError('');
                }}
              >
                Resolve
              </button>
              {selected === component.occurrence_id && (
                <div>
                  <label>
                    Item number{' '}
                    <input value={search} onChange={(event) => setSearch(event.target.value)} />
                  </label>
                  <button type="button" disabled={disabled || busy} onClick={() => void find()}>
                    Find Items
                  </button>
                  {matches.map((item) => (
                    <button
                      type="button"
                      key={item.id}
                      disabled={disabled || busy}
                      onClick={() => void add(component, item.current_revision_id!)}
                    >
                      Use {item.item_number} · {item.name} · {item.item_type} · Rev{' '}
                      {item.current_revision_code}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </section>
  );
}
