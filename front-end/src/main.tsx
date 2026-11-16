import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from 'react-router-dom';
import type {
  ColumnDef,
  ColumnFiltersState,
  ColumnSizingState,
  PaginationState,
  SortingState,
  Updater,
  VisibilityState,
} from '@tanstack/react-table';
import { Button } from './components/Button';
import { ItemWorkspace } from './pages/ItemWorkspace';
import { AdminConsole } from './pages/admin/AdminConsole';
const CheckinWorkflow = lazy(() =>
  import('./pages/checkinNew/CheckinWorkflow').then((module) => ({
    default: module.CheckinWorkflow,
  }))
);
const AdvancedSearch = lazy(() =>
  import('./pages/AdvancedSearch').then((module) => ({ default: module.AdvancedSearch }))
);
const Reviews = lazy(() =>
  import('./pages/ReviewInbox').then((module) => ({ default: module.ReviewInbox }))
);
const Notifications = lazy(() =>
  import('./pages/Notifications').then((module) => ({ default: module.Notifications }))
);
const Assistant = lazy(() =>
  import('./pages/AiAssistant').then((module) => ({ default: module.AiAssistant }))
);
const Comparison = lazy(() =>
  import('./pages/Comparison').then((module) => ({ default: module.ComparisonPage }))
);
const AttachmentEditor = lazy(() =>
  import('./pages/EditItemAttachment').then((module) => ({
    default: module.EditItemAttachmentPage,
  }))
);
const CADReview = lazy(() =>
  import('./pages/CADReview').then((module) => ({ default: module.CADReviewPage }))
);
import { StagesPage, type StageProcessingJob } from './pages/Stages';
import type { ReviewInboxEntry } from './workflow-api';
import { BLANK_FILTER_VALUE, DataTable } from './components/DataTable';
import {
  api,
  downloadManagedFile,
  login,
  type Session,
  type Item,
  type SearchRow,
  type SearchResponse,
  type Upload,
} from './api';
import './styles.css';

function useLoad<T>(load: () => Promise<T>, deps: React.DependencyList) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError('');
    load()
      .then((value) => {
        if (live) setData(value);
      })
      .catch((reason) => {
        if (live) setError(reason instanceof Error ? reason.message : 'Unable to load data');
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [...deps, attempt]);
  return {
    data,
    error,
    loading,
    retry: () => setAttempt((value) => value + 1),
  };
}
function State({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  return loading ? (
    <p role="status">Loading…</p>
  ) : error ? (
    <div className="error" role="alert">
      {error}{' '}
      <Button variant="secondary" onClick={retry}>
        Retry
      </Button>
    </div>
  ) : null;
}
function SignIn({ onSession }: { onSession: (session: Session) => void }) {
  const [url, setUrl] = useState(
    () =>
      localStorage.getItem('modelryx-server') || `${location.protocol}//${location.hostname}:8000`
  );
  const [username, setUsername] = useState(() => localStorage.getItem('modelryx-username') || '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await login(url.trim(), username.trim(), password);
      localStorage.setItem('modelryx-server', url.trim().replace(/\/$/, ''));
      localStorage.setItem('modelryx-username', username.trim());
      localStorage.setItem('modelryx-token', result.access_token);
      onSession(await api<Session>('/session'));
    } catch (reason) {
      localStorage.removeItem('modelryx-token');
      setError(reason instanceof Error ? reason.message : 'Unable to sign in');
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="signin">
      <form className="signin-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark">P</span> Modelryx
        </div>
        <h1>Welcome back</h1>
        <p>Sign in to your engineering workspace.</p>
        <label>
          Server URL
          <input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label>
          Username
          <input
            required
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            required
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </main>
  );
}
const itemColumns: ColumnDef<Item>[] = [
  {
    accessorKey: 'item_number',
    header: 'Item number',
    enableHiding: false,
    cell: ({ row }) => (
      <Link to={`/items/${encodeURIComponent(row.original.id)}`}>{row.original.item_number}</Link>
    ),
  },
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'item_type', header: 'Type' },
  { accessorKey: 'status', header: 'Item status' },
];
function Home({ session }: { session: Session }) {
  const state = useLoad(
    () =>
      api<{
        recent: Item[];
        favorites: Item[];
        activity: {
          action: string;
          occurred_at: string;
          entity_id: string | null;
        }[];
      }>('/me/home'),
    []
  );
  const canReview = session.roles.some((role) => role === 'reviewer' || role === 'administrator');
  const work = useLoad(async () => {
    const [workspace, reviews, processingJobs] = await Promise.all([
      api<{ checked_out: Item[]; uploads: Upload[] }>('/me/workspace'),
      canReview
        ? api<ReviewInboxEntry[]>('/approval-requests/inbox').catch(() => null)
        : Promise.resolve([]),
      api<StageProcessingJob[]>('/processing-jobs?state=all&limit=100').catch(() => null),
    ]);
    return { workspace, reviews, processingJobs };
  }, [canReview]);
  const failedTasks =
    work.data?.workspace.uploads.filter((task) => !task.is_archived && task.status === 'FAILED') ??
    [];
  const activeTasks =
    work.data?.workspace.uploads.filter(
      (task) => !task.is_archived && !task.is_finished && task.status !== 'FAILED'
    ) ?? [];
  return (
    <>
      <div className="page-header">
        <div>
          <div className="eyebrow">YOUR WORKSPACE</div>
          <h1>Welcome back, {session.display_name}</h1>
          <p className="intro">Find your product data and pick up where you left off.</p>
        </div>
        <Link className="button button--primary button-link" to="/library">
          Explore library
        </Link>
      </div>
      <State {...state} />
      <section className="home-work" aria-label="Work requiring attention">
        <div className="section-heading">
          <div>
            <h2>My work</h2>
            <p>Continue engineering tasks and review requests</p>
          </div>
          <Link to="/workspace">Open workspace</Link>
        </div>
        <State {...work} />
        {!work.loading && !work.error && work.data && (
          <div className="home-work-grid">
            <Link to="/workspace">
              <strong>{work.data.workspace.checked_out.length}</strong>
              <span>Checked-out items</span>
            </Link>
            <Link to="/workspace">
              <strong>{activeTasks.length}</strong>
              <span>Tasks in progress</span>
            </Link>
            <Link to="/workspace">
              <strong>{failedTasks.length}</strong>
              <span>Failed tasks to resolve</span>
            </Link>
            <Link to="/stages">
              <strong>
                {work.data.processingJobs
                  ? work.data.processingJobs.filter((job) => job.state === 'FAILED').length
                  : '—'}
              </strong>
              <span>
                {work.data.processingJobs
                  ? 'Recent failed processing jobs'
                  : 'Processing status unavailable'}
              </span>
            </Link>
            {canReview && (
              <Link to="/reviews">
                <strong>{work.data.reviews?.length ?? '—'}</strong>
                <span>
                  {work.data.reviews ? 'Reviews awaiting decision' : 'Review status unavailable'}
                </span>
              </Link>
            )}
          </div>
        )}
        {!work.loading && !work.error && work.data && !!work.data.reviews?.length && (
          <div className="home-review-queue">
            <h3>Next reviews</h3>
            <ul>
              {[...work.data.reviews]
                .sort(
                  (a, b) =>
                    new Date(a.approval.due_at).getTime() - new Date(b.approval.due_at).getTime()
                )
                .slice(0, 3)
                .map((entry) => (
                  <li key={entry.approval.id}>
                    <Link
                      to={`/items/${encodeURIComponent(entry.item_id)}/revisions/${encodeURIComponent(entry.approval.revision_id)}`}
                    >
                      {entry.item_number} · Revision {entry.revision_code}
                    </Link>
                    <span>Due {new Date(entry.approval.due_at).toLocaleString()}</span>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </section>
      {!state.loading && !state.error && state.data && (
        <div className="home-grid">
          <section>
            <div className="section-heading">
              <div>
                <h2>Recently viewed</h2>
                <p>Return to your recent work</p>
              </div>
              <Link to="/recent">View all</Link>
            </div>
            <DataTable
              data={state.data.recent}
              columns={itemColumns}
              getRowId={(row) => row.id}
              emptyMessage="No recently viewed items."
            />
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>Favorites</h2>
                <p>Your saved items</p>
              </div>
              <Link to="/favorites">View all</Link>
            </div>
            <DataTable
              data={state.data.favorites}
              columns={itemColumns}
              getRowId={(row) => row.id}
              emptyMessage="No favorites yet."
            />
          </section>
        </div>
      )}
    </>
  );
}
const searchColumns: ColumnDef<SearchRow>[] = [
  {
    id: 'item_number',
    header: 'Item number',
    accessorKey: 'item_number',
    enableHiding: false,
    cell: ({ row }) => (
      <Link to={`/items/${encodeURIComponent(row.original.item_id)}`}>
        {row.original.item_number}
      </Link>
    ),
  },
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'item_type', header: 'Type' },
  {
    id: 'revision',
    header: 'Revision',
    accessorFn: (row) => row.revision_code || '—',
    cell: ({ row }) =>
      row.original.revision_id ? (
        <Link
          to={`/items/${encodeURIComponent(row.original.item_id)}/revisions/${encodeURIComponent(row.original.revision_id)}`}
        >
          {row.original.revision_code || 'Open revision'}
        </Link>
      ) : (
        '—'
      ),
  },
  {
    id: 'cad',
    header: 'CAD system',
    accessorFn: (row) => row.cad_system || '—',
  },
  {
    id: 'status',
    header: 'Item status',
    accessorFn: (row) => row.item_status || '—',
  },
];
type LibraryView = {
  id?: string;
  scope?: 'PERSONAL' | 'SHARED';
  can_edit?: boolean;
  name: string;
  query: string;
  itemName: string;
  filters: ColumnFiltersState;
  sorting: SortingState;
  visibility: VisibilityState;
  sizing: ColumnSizingState;
  pageSize: number;
  density: 'comfortable' | 'compact';
};
type SavedLibraryView = {
  id: string;
  name: string;
  scope: 'PERSONAL' | 'SHARED';
  can_edit: boolean;
  settings: Omit<LibraryView, 'id' | 'name' | 'scope' | 'can_edit'>;
};
function loadLibraryViews(key: string): LibraryView[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(parsed)
      ? parsed.filter(
          (entry): entry is LibraryView =>
            typeof entry === 'object' &&
            entry !== null &&
            typeof entry.name === 'string' &&
            Array.isArray(entry.filters) &&
            Array.isArray(entry.sorting)
        )
      : [];
  } catch {
    return [];
  }
}
function Library({
  canCreate,
  username,
  isAdmin,
}: {
  canCreate: boolean;
  username: string;
  isAdmin: boolean;
}) {
  const viewsKey = `modelryx-library-views:${username}`;
  const [views, setViews] = useState<LibraryView[]>(() => loadLibraryViews(viewsKey));
  const [selectedView, setSelectedView] = useState('');
  const [viewName, setViewName] = useState('');
  const [viewScope, setViewScope] = useState<'PERSONAL' | 'SHARED'>('PERSONAL');
  const [viewBusy, setViewBusy] = useState(false);
  const [viewError, setViewError] = useState('');
  const [workerCadSystems, setWorkerCadSystems] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    api<Array<{ cadSystem: string }>>('/workers/external-formats')
      .then((workers) => {
        if (active) setWorkerCadSystems(workers.map((worker) => worker.cadSystem).filter(Boolean));
      })
      .catch(() => {
        if (active) setWorkerCadSystems([]);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    api<SavedLibraryView[]>('/ui-views')
      .then((rows) => {
        if (!active) return;
        const serverViews: LibraryView[] = rows.map((row) => ({
          ...row.settings,
          id: row.id,
          name: row.name,
          scope: row.scope,
          can_edit: row.can_edit,
        }));
        const legacy = loadLibraryViews(viewsKey).filter(
          (row) =>
            !serverViews.some((saved) => saved.scope === 'PERSONAL' && saved.name === row.name)
        );
        setViews([...serverViews, ...legacy].sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch((reason) => {
        if (active)
          setViewError(reason instanceof Error ? reason.message : 'Unable to load saved views');
      });
    return () => {
      active = false;
    };
  }, [viewsKey]);
  const [visibility, setVisibility] = useState<VisibilityState>({});
  const [sizing, setSizing] = useState<ColumnSizingState>({});
  const [density, setDensity] = useState<'comfortable' | 'compact'>('comfortable');
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get('q') || '');
  const [name, setName] = useState(params.get('name') || '');
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filters, setFilters] = useState<ColumnFiltersState>([]);
  const navigate = useNavigate();
  const appliedName = params.get('name') || '';
  const appliedQuery = params.get('q') || '';
  const sortFields: Record<string, string> = {
    item_number: 'item_number',
    name: 'name',
    item_type: 'item_type',
    revision: 'revision',
    cad: 'cad_system',
    status: 'item_status',
  };
  const filterFields: Record<string, string> = {
    item_type: 'item_types',
    cad: 'cad_systems',
    status: 'item_statuses',
  };
  const requestFilters: Record<string, unknown> = {
    ...(appliedQuery ? { item_number_regex: appliedQuery } : {}),
    ...(appliedName ? { name_regex: appliedName } : {}),
  };
  for (const filter of filters) {
    const field = filterFields[filter.id];
    if (field && Array.isArray(filter.value))
      requestFilters[field] = filter.value.map((value) =>
        value === BLANK_FILTER_VALUE ? null : value
      );
  }
  const payload = {
    entity_type: 'item',
    filters: requestFilters,
    limit: pagination.pageSize,
    offset: pagination.pageIndex * pagination.pageSize,
    include_facets: true,
    ...(sorting.length
      ? {
          sort: [
            {
              field: sortFields[sorting[0].id],
              direction: sorting[0].desc ? 'desc' : 'asc',
            },
          ],
        }
      : {}),
  };
  const requestKey = JSON.stringify(payload);
  const state = useLoad(() => api<SearchResponse>('/search', 'POST', payload), [requestKey]);
  useEffect(() => {
    if (!state.loading && !state.error && state.data) {
      const lastPage = Math.max(0, Math.ceil(state.data.total / pagination.pageSize) - 1);
      if (pagination.pageIndex > lastPage)
        setPagination((current) => ({ ...current, pageIndex: lastPage }));
    }
  }, [state.loading, state.error, state.data, pagination.pageIndex, pagination.pageSize]);
  const changePagination = (updater: Updater<PaginationState>) =>
    setPagination((current) => {
      const next = typeof updater === 'function' ? updater(current) : updater;
      return next.pageSize !== current.pageSize ? { ...next, pageIndex: 0 } : next;
    });
  const changeSorting = (updater: Updater<SortingState>) => {
    setSorting(updater);
    setSelectedView('');
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  };
  const changeFilters = (updater: Updater<ColumnFiltersState>) => {
    setFilters(updater);
    setSelectedView('');
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  };
  const updateVisibility = (updater: Updater<VisibilityState>) => {
    setVisibility(updater);
    setSelectedView('');
  };
  const updateSizing = (updater: Updater<ColumnSizingState>) => {
    setSizing(updater);
    setSelectedView('');
  };
  const saveView = async () => {
    const label = viewName.trim().slice(0, 50);
    if (!label || viewBusy) return;
    const existing = views.find(
      (entry) => entry.name === label && entry.scope === viewScope && entry.id && entry.can_edit
    );
    const settings = {
      query: appliedQuery,
      itemName: appliedName,
      filters,
      sorting,
      visibility,
      sizing,
      pageSize: pagination.pageSize,
      density,
    };
    setViewBusy(true);
    setViewError('');
    try {
      const saved = await api<SavedLibraryView>(
        existing?.id ? `/ui-views/${encodeURIComponent(existing.id)}` : '/ui-views',
        existing?.id ? 'PUT' : 'POST',
        { name: label, scope: viewScope, settings }
      );
      setViews((current) =>
        [
          ...current.filter(
            (entry) => entry.id !== saved.id && !(entry.name === label && !entry.id)
          ),
          {
            ...saved.settings,
            id: saved.id,
            name: saved.name,
            scope: saved.scope,
            can_edit: saved.can_edit,
          },
        ].sort((a, b) => a.name.localeCompare(b.name))
      );
      localStorage.setItem(
        viewsKey,
        JSON.stringify(loadLibraryViews(viewsKey).filter((row) => row.name !== label))
      );
      setSelectedView(saved.id);
      setViewName(saved.name);
    } catch (reason) {
      setViewError(reason instanceof Error ? reason.message : 'Unable to save view');
    } finally {
      setViewBusy(false);
    }
  };
  const applyView = (key: string) => {
    setSelectedView(key);
    const view = views.find((entry) => (entry.id ?? `local:${entry.name}`) === key);
    if (!view) return;
    setQuery(view.query);
    setName(view.itemName);
    setFilters(view.filters);
    setSorting(view.sorting);
    setVisibility(view.visibility ?? {});
    setSizing(view.sizing ?? {});
    setDensity(view.density === 'compact' ? 'compact' : 'comfortable');
    setPagination({ pageIndex: 0, pageSize: view.pageSize || 10 });
    setParams({
      ...(view.query ? { q: view.query } : {}),
      ...(view.itemName ? { name: view.itemName } : {}),
    });
    setViewName(view.name);
    setViewScope(view.scope === 'SHARED' && !isAdmin ? 'PERSONAL' : (view.scope ?? 'PERSONAL'));
  };
  const deleteView = async () => {
    if (!selectedView) return;
    const selected = views.find((entry) => (entry.id ?? `local:${entry.name}`) === selectedView);
    if (!selected || selected.can_edit === false || viewBusy) return;
    setViewBusy(true);
    setViewError('');
    try {
      if (selected.id) await api(`/ui-views/${encodeURIComponent(selected.id)}`, 'DELETE');
      else
        localStorage.setItem(
          viewsKey,
          JSON.stringify(loadLibraryViews(viewsKey).filter((row) => row.name !== selected.name))
        );
      setViews((current) => current.filter((entry) => entry !== selected));
      setSelectedView('');
      setViewName('');
    } catch (reason) {
      setViewError(reason instanceof Error ? reason.message : 'Unable to delete view');
    } finally {
      setViewBusy(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSelectedView('');
    setPagination((current) => ({ ...current, pageIndex: 0 }));
    setParams({
      ...(query.trim() ? { q: query.trim() } : {}),
      ...(name.trim() ? { name: name.trim() } : {}),
    });
  };
  const values = (items: (string | null)[] | undefined) =>
    (items ?? []).map((value) => value || BLANK_FILTER_VALUE);
  return (
    <>
      <div className="eyebrow">PRODUCT DATA</div>
      <div className="page-header">
        <h1>Library</h1>
        {canCreate && (
          <Link className="button button--primary button-link" to="/checkin">
            New Item
          </Link>
        )}
      </div>
      <p className="intro">Find parts and assemblies across your product data.</p>
      <form className="filters" onSubmit={submit}>
        <label>
          Item number pattern
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search item number"
          />
        </label>
        <label>
          Name pattern
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Search name"
          />
        </label>
        <Button type="submit">Search</Button>
        <Button
          variant="secondary"
          onClick={() => {
            setQuery('');
            setName('');
            setSelectedView('');
            setFilters([]);
            setPagination((current) => ({ ...current, pageIndex: 0 }));
            setParams({});
          }}
        >
          Clear
        </Button>
      </form>
      <div className="library-views" aria-label="Library views">
        <label>
          Saved view{' '}
          <select value={selectedView} onChange={(event) => applyView(event.target.value)}>
            <option value="">Current settings</option>
            {views.map((view) => (
              <option value={view.id ?? `local:${view.name}`} key={view.id ?? `local:${view.name}`}>
                {view.name}
                {view.scope === 'SHARED' ? ' · Shared' : !view.id ? ' · This browser' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          View name{' '}
          <input
            value={viewName}
            maxLength={50}
            onChange={(event) => setViewName(event.target.value)}
            placeholder="e.g. Active CAD parts"
          />
        </label>
        {isAdmin && (
          <label>
            Visibility{' '}
            <select
              value={viewScope}
              onChange={(event) => {
                setViewScope(event.target.value === 'SHARED' ? 'SHARED' : 'PERSONAL');
                setSelectedView('');
              }}
            >
              <option value="PERSONAL">Only me</option>
              <option value="SHARED">All users</option>
            </select>
          </label>
        )}
        <Button
          variant="secondary"
          disabled={!viewName.trim() || viewBusy}
          onClick={() => void saveView()}
        >
          {viewBusy ? 'Saving…' : 'Save current view'}
        </Button>
        <Button
          variant="ghost"
          disabled={
            !selectedView ||
            views.find((entry) => (entry.id ?? `local:${entry.name}`) === selectedView)
              ?.can_edit === false ||
            viewBusy
          }
          onClick={() => void deleteView()}
        >
          Delete view
        </Button>
        <label>
          Row density{' '}
          <select
            value={density}
            onChange={(event) => {
              setDensity(event.target.value === 'compact' ? 'compact' : 'comfortable');
              setSelectedView('');
            }}
          >
            <option value="comfortable">Comfortable</option>
            <option value="compact">Compact</option>
          </select>
        </label>
        <small>
          Personal views follow your account. Administrators can publish a view to all users.
        </small>
        {viewError && (
          <p className="error" role="alert">
            Saved views: {viewError}
          </p>
        )}
      </div>
      <State {...state} />
      {!state.error && state.data && (
        <>
          <p className="result-meta">
            Showing {state.data.results.length ? pagination.pageIndex * pagination.pageSize + 1 : 0}
            –{pagination.pageIndex * pagination.pageSize + state.data.results.length} of{' '}
            {state.data.total} results.
          </p>
          {state.data.warnings?.map((w) => (
            <p className="warning" key={w}>
              {w}
            </p>
          ))}
          <DataTable
            data={state.data.results}
            columns={searchColumns}
            filterColumnIds={['item_type', 'cad', 'status']}
            getRowId={(row) => `${row.item_id}:${row.revision_id ?? ''}`}
            loading={state.loading}
            layout={{
              visibility,
              sizing,
              onVisibilityChange: updateVisibility,
              onSizingChange: updateSizing,
            }}
            density={density}
            server={{
              total: state.data.total,
              pagination,
              onPaginationChange: changePagination,
              sorting,
              onSortingChange: changeSorting,
              filters,
              onFiltersChange: changeFilters,
              filterOptions: {
                item_type: values(state.data.facets?.item_types),
                cad: (() => {
                  const existing = values(state.data.facets?.cad_systems);
                  const seen = new Set(existing.map((value) => value.toLocaleLowerCase()));
                  return [
                    ...existing,
                    ...workerCadSystems.filter((system) => {
                      const key = system.toLocaleLowerCase();
                      if (seen.has(key)) return false;
                      seen.add(key);
                      return true;
                    }),
                  ];
                })(),
                status: values(state.data.facets?.item_statuses),
              },
            }}
            rowActions={(row) => [
              {
                label: 'Open item',
                onSelect: () => navigate(`/items/${encodeURIComponent(row.item_id)}`),
              },
              ...(row.revision_id
                ? [
                    {
                      label: 'Open revision',
                      onSelect: () =>
                        navigate(
                          `/items/${encodeURIComponent(row.item_id)}/revisions/${encodeURIComponent(row.revision_id!)}`
                        ),
                    },
                  ]
                : []),
            ]}
          />
        </>
      )}
    </>
  );
}
function PersonalItems({ kind }: { kind: 'recent' | 'favorites' }) {
  const state = useLoad(
    async () =>
      kind === 'favorites'
        ? {
            favorites: (await api<{ items: { item: Item }[] }>('/items/favorites')).items.map(
              (entry) => entry.item
            ),
            activity: [] as Activity[],
          }
        : {
            favorites: [] as Item[],
            activity: await api<Activity[]>('/me/activity?limit=100'),
          },
    [kind]
  );
  const activityColumns: ColumnDef<Activity>[] = [
    {
      accessorKey: 'action',
      header: 'Activity',
      cell: ({ getValue }) => String(getValue()).replaceAll('.', ' '),
    },
    { accessorKey: 'entity_type', header: 'Type' },
    {
      accessorKey: 'occurred_at',
      header: 'When',
      cell: ({ getValue }) => new Date(String(getValue())).toLocaleString(),
    },
  ];
  return (
    <>
      <div className="eyebrow">PRODUCT DATA</div>
      <h1>{kind === 'recent' ? 'Recent activity' : 'Favorites'}</h1>
      <p className="intro">
        {kind === 'recent'
          ? 'Your latest activity, up to 100 entries.'
          : 'Items you have saved for quick access.'}
      </p>
      <State {...state} />
      {state.data &&
        !state.error &&
        (kind === 'favorites' ? (
          <DataTable
            data={state.data.favorites}
            columns={itemColumns}
            getRowId={(row) => row.id}
            loading={state.loading}
          />
        ) : (
          <DataTable
            data={state.data.activity}
            columns={activityColumns}
            getRowId={(_row, index) => String(index)}
            loading={state.loading}
          />
        ))}
    </>
  );
}
type Activity = { action: string; entity_type: string; occurred_at: string };
function Workspace() {
  const state = useLoad(() => api<{ checked_out: Item[]; uploads: Upload[] }>('/me/workspace'), []);
  const navigate = useNavigate();
  const [scope, setScope] = useState('active');
  const [workspaceQuery, setWorkspaceQuery] = useState('');
  const [taskError, setTaskError] = useState('');
  const [busyTask, setBusyTask] = useState('');
  const taskEditUrl = (task: Upload) => {
    if (task.operation === 'EDIT_REVISION_ATTACHMENT' && task.item_id && task.revision_id)
      return `/items/${encodeURIComponent(task.item_id)}/revisions/${encodeURIComponent(task.revision_id)}/attachments/edit`;
    if (task.operation === 'EDIT_ITEM_ATTACHMENT' && task.item_id)
      return `/items/${encodeURIComponent(task.item_id)}/attachments/edit`;
    if (task.operation === 'EDIT_ITEM_REVISION' && task.item_id && task.revision_id)
      return `/items/${encodeURIComponent(task.item_id)}/revisions/${encodeURIComponent(task.revision_id)}?section=edit`;
    return `/checkin?${new URLSearchParams({ resumeStageId: task.id, taskName: task.primary_filename ?? task.item_name ?? task.item_number ?? '' })}`;
  };
  const changeTask = async (
    task: Upload,
    action: 'ARCHIVE' | 'RESTORE' | 'REMOVE' | 'RETRY' | 'DOWNLOAD'
  ) => {
    if (busyTask) return;
    if (
      action === 'REMOVE' &&
      !window.confirm(
        task.is_finished
          ? 'Remove this finished task from Workspace?'
          : 'Remove this task from Workspace? Unfinished staging may be cancelled.'
      )
    )
      return;
    setBusyTask(task.id);
    setTaskError('');
    try {
      if (action === 'DOWNLOAD') {
        if (task.download_url)
          await downloadManagedFile(task.download_url, task.primary_filename || 'cad-files.zip');
      } else if (action === 'RETRY')
        await api(`/download-bundles/${encodeURIComponent(task.id)}/retry`, 'POST');
      else if (task.operation === 'DOWNLOAD_CAD_FILES')
        await api(`/download-bundles/${encodeURIComponent(task.id)}/workspace`, 'PATCH', {
          action,
        });
      else if (
        action === 'REMOVE' &&
        !task.is_finished &&
        ['EDIT_ITEM_ATTACHMENT', 'EDIT_REVISION_ATTACHMENT'].includes(task.operation) &&
        task.item_id
      ) {
        const endpoint =
          task.operation === 'EDIT_REVISION_ATTACHMENT' && task.revision_id
            ? `/items/${encodeURIComponent(task.item_id)}/revisions/${encodeURIComponent(task.revision_id)}/attachment-edit`
            : `/items/${encodeURIComponent(task.item_id)}/attachment-edit`;
        await api(endpoint, 'DELETE');
        await api(`/staging/${encodeURIComponent(task.id)}/workspace`, 'PATCH', {
          action: 'REMOVE',
        });
      } else if (action === 'REMOVE' && !task.is_finished)
        await api(`/staging/${encodeURIComponent(task.id)}`, 'DELETE');
      else await api(`/staging/${encodeURIComponent(task.id)}/workspace`, 'PATCH', { action });
      state.retry();
    } catch (reason) {
      setTaskError(reason instanceof Error ? reason.message : 'Unable to update task');
    } finally {
      setBusyTask('');
    }
  };
  const cols: ColumnDef<Upload>[] = [
    {
      id: 'name',
      header: 'Item or file',
      accessorFn: (r) => r.item_number || r.primary_filename || r.item_name || '—',
    },
    { accessorKey: 'operation', header: 'Operation' },
    {
      id: 'state',
      header: 'Workflow state',
      accessorFn: (r) => r.workflow_state || r.status,
    },
    {
      accessorKey: 'progress_percent',
      header: 'Transfer progress',
      cell: ({ getValue }) => `${getValue()}%`,
    },
  ];
  return (
    <>
      <div className="eyebrow">MY WORK</div>
      <h1>Workspace</h1>
      <p className="intro">Your checked-out items and upload tasks.</p>
      <div className="workspace-search">
        <label>
          Search workspace{' '}
          <input
            value={workspaceQuery}
            onChange={(event) => setWorkspaceQuery(event.target.value)}
            placeholder="Item number, name or file"
          />
        </label>
        <Button variant="secondary" onClick={state.retry}>
          Refresh
        </Button>
      </div>
      <State {...state} />
      {taskError && (
        <p role="alert" className="error">
          {taskError}
        </p>
      )}
      {state.data && !state.error && (
        <div className="stack">
          <section>
            <div className="section-heading">
              <div>
                <h2>Checked out</h2>
                <p>Items in your workspace</p>
              </div>
            </div>
            <DataTable
              data={state.data.checked_out.filter((row) =>
                `${row.item_number} ${row.name}`
                  .toLocaleLowerCase()
                  .includes(workspaceQuery.trim().toLocaleLowerCase())
              )}
              columns={[
                ...itemColumns,
                {
                  id: 'revision',
                  header: 'Workspace revision',
                  accessorFn: (r) => r.workspace_revision_code || r.current_revision_code || '—',
                  cell: ({ row }) =>
                    row.original.workspace_revision_id ? (
                      <Link
                        to={`/items/${row.original.id}/revisions/${row.original.workspace_revision_id}`}
                      >
                        {row.original.workspace_revision_code || 'Open revision'}
                      </Link>
                    ) : (
                      row.original.current_revision_code || '—'
                    ),
                },
              ]}
              getRowId={(row) => row.id}
              rowActions={(row) =>
                row.workspace_revision_id
                  ? [
                      {
                        label: 'Continue editing',
                        onSelect: () =>
                          navigate(
                            `/items/${encodeURIComponent(row.id)}/revisions/${encodeURIComponent(row.workspace_revision_id!)}?section=edit`
                          ),
                      },
                    ]
                  : []
              }
              emptyMessage="No checked-out items."
            />
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>Upload tasks</h2>
                <p>Transfer and staging activity</p>
              </div>
            </div>
            <label>
              Tasks{' '}
              <select value={scope} onChange={(event) => setScope(event.target.value)}>
                <option value="active">Active</option>
                <option value="archived">Archived</option>
                <option value="all">All</option>
              </select>
            </label>
            <DataTable
              data={state.data.uploads.filter(
                (row) =>
                  (scope === 'all' ||
                    (scope === 'archived' ? row.is_archived : !row.is_archived)) &&
                  `${row.item_number ?? ''} ${row.item_name ?? ''} ${row.primary_filename ?? ''} ${row.operation}`
                    .toLocaleLowerCase()
                    .includes(workspaceQuery.trim().toLocaleLowerCase())
              )}
              columns={cols}
              getRowId={(row) => row.id}
              rowActions={(task) => [
                ...(![
                  'EDIT_ITEM_ATTACHMENT',
                  'EDIT_REVISION_ATTACHMENT',
                  'DOWNLOAD_CAD_FILES',
                ].includes(task.operation)
                  ? [
                      {
                        label: 'View status',
                        onSelect: () => navigate(`/stages?stageId=${encodeURIComponent(task.id)}`),
                      },
                    ]
                  : []),
                ...(task.is_finished && task.item_id && task.revision_id
                  ? [
                      {
                        label: 'Open revision',
                        onSelect: () =>
                          navigate(
                            `/items/${encodeURIComponent(task.item_id!)}/revisions/${encodeURIComponent(task.revision_id!)}`
                          ),
                      },
                    ]
                  : []),
                ...(task.can_edit || task.can_resume
                  ? [{ label: 'Continue', onSelect: () => navigate(taskEditUrl(task)) }]
                  : []),
                ...(task.download_url
                  ? [
                      {
                        label: 'Download',
                        disabled: !!busyTask,
                        onSelect: () => void changeTask(task, 'DOWNLOAD'),
                      },
                    ]
                  : []),
                ...(task.operation === 'DOWNLOAD_CAD_FILES' && task.status === 'FAILED'
                  ? [
                      {
                        label: 'Retry',
                        disabled: !!busyTask,
                        onSelect: () => void changeTask(task, 'RETRY'),
                      },
                    ]
                  : []),
                {
                  label: task.is_archived ? 'Restore' : 'Archive',
                  disabled: !!busyTask,
                  onSelect: () => void changeTask(task, task.is_archived ? 'RESTORE' : 'ARCHIVE'),
                },
                {
                  label: 'Remove',
                  destructive: true,
                  disabled: !!busyTask,
                  onSelect: () => void changeTask(task, 'REMOVE'),
                },
              ]}
              emptyMessage="No tasks."
            />
          </section>
        </div>
      )}
    </>
  );
}
function Shell({ session, signOut }: { session: Session; signOut: () => void }) {
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    let active = true;
    const refresh = () => {
      void api<unknown[]>('/me/notifications?unread_only=true&limit=100')
        .then((rows) => {
          if (active) setUnread(rows.length);
        })
        .catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, 60000);
    window.addEventListener('modelryx-notifications-changed', refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener('modelryx-notifications-changed', refresh);
    };
  }, []);
  const [dark, setDark] = useState(() => localStorage.getItem('modelryx-new-theme') === 'dark');
  return (
    <div className="app" data-theme={dark ? 'dark' : 'light'}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" to="/">
          <span className="brand-mark">P</span> Modelryx <span className="tag">NEW UI</span>
        </Link>
        <div className="top-actions">
          <span>{session.display_name}</span>
          <Button
            variant="secondary"
            onClick={() =>
              setDark((current) => {
                localStorage.setItem('modelryx-new-theme', current ? 'light' : 'dark');
                return !current;
              })
            }
          >
            {dark ? 'Light mode' : 'Dark mode'}
          </Button>
          <Button variant="ghost" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </header>
      <div className="shell">
        <nav className="sidebar" aria-label="Main navigation">
          <NavLink to="/" end>
            Home
          </NavLink>
          <div className="sidebar-label">PRODUCT DATA</div>
          <NavLink to="/library">Library</NavLink>
          <NavLink to="/engineering-search">Engineering search</NavLink>
          <NavLink to="/favorites">Favorites</NavLink>
          <NavLink to="/recent">Recent</NavLink>
          <div className="sidebar-label">MY WORK</div>
          <NavLink to="/workspace">Workspace</NavLink>
          {session.roles.some((role) =>
            ['designer', 'engineer', 'reviewer', 'administrator'].includes(role)
          ) && <NavLink to="/checkin">New Item</NavLink>}
          <NavLink to="/stages">Stages</NavLink>
          <NavLink to="/notifications">Notifications{unread > 0 && ` (${unread})`}</NavLink>
          {session.roles.some((role) => ['reviewer', 'administrator'].includes(role)) && (
            <NavLink to="/reviews">Reviews</NavLink>
          )}
          <NavLink to="/ai">AI Assistant</NavLink>
          {session.roles.includes('administrator') && (
            <>
              <div className="sidebar-label">SYSTEM</div>
              <NavLink to="/admin">Administration</NavLink>
            </>
          )}
        </nav>
        <main id="main" className="content">
          <Routes>
            <Route path="/" element={<Home session={session} />} />
            <Route
              path="/engineering-search"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <AdvancedSearch />
                </Suspense>
              }
            />
            <Route
              path="/library"
              element={
                <Library
                  username={session.subject}
                  isAdmin={session.roles.includes('administrator')}
                  canCreate={session.roles.some((role) =>
                    ['designer', 'engineer', 'reviewer', 'administrator'].includes(role)
                  )}
                />
              }
            />
            <Route
              path="/checkin"
              element={
                session.roles.some((role) =>
                  ['designer', 'engineer', 'reviewer', 'administrator'].includes(role)
                ) ? (
                  <Suspense fallback={<p role="status">Loading New Item…</p>}>
                    <CheckinWorkflow />
                  </Suspense>
                ) : (
                  <p role="alert">Creating items requires engineering access.</p>
                )
              }
            />
            <Route
              path="/stages"
              element={
                <Suspense fallback={<p role="status">Loading stages…</p>}>
                  <StagesPage isAdmin={session.roles.includes('administrator')} />
                </Suspense>
              }
            />
            <Route path="/recent" element={<PersonalItems kind="recent" />} />
            <Route path="/favorites" element={<PersonalItems kind="favorites" />} />
            <Route path="/workspace" element={<Workspace />} />
            <Route
              path="/notifications"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <Notifications />
                </Suspense>
              }
            />
            <Route
              path="/reviews"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <Reviews
                    canReview={session.roles.some((role) =>
                      ['reviewer', 'administrator'].includes(role)
                    )}
                  />
                </Suspense>
              }
            />
            <Route
              path="/ai"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <Assistant />
                </Suspense>
              }
            />
            <Route
              path="/items/:itemId/attachments/edit"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <AttachmentEditor />
                </Suspense>
              }
            />
            <Route
              path="/items/:itemId/revisions/:revisionId/attachments/edit"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <AttachmentEditor />
                </Suspense>
              }
            />
            <Route
              path="/items/:itemId/compare"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <Comparison />
                </Suspense>
              }
            />
            <Route
              path="/items/:itemId/revisions/:revisionId/cad-sessions/:sessionId"
              element={
                <Suspense fallback={<p>Loading…</p>}>
                  <CADReview />
                </Suspense>
              }
            />
            <Route
              path="/items/:itemId/revisions/:revisionId/generate-preview"
              element={<Navigate to="..?section=generate" relative="path" replace />}
            />
            <Route
              path="/admin"
              element={
                <AdminConsole
                  isAdmin={session.roles.includes('administrator')}
                  currentUsername={session.subject}
                />
              }
            />
            <Route
              path="/items/:itemId"
              element={
                <ItemWorkspace
                  isAdmin={session.roles.includes('administrator')}
                  canReview={session.roles.some((role) =>
                    ['reviewer', 'administrator'].includes(role)
                  )}
                  canRelease={session.roles.some((role) =>
                    ['designer', 'engineer', 'administrator'].includes(role)
                  )}
                  canEdit={session.roles.some((role) =>
                    ['designer', 'engineer', 'reviewer', 'administrator'].includes(role)
                  )}
                />
              }
            />
            <Route
              path="/items/:itemId/revisions/:revisionId"
              element={
                <ItemWorkspace
                  isAdmin={session.roles.includes('administrator')}
                  canReview={session.roles.some((role) =>
                    ['reviewer', 'administrator'].includes(role)
                  )}
                  canRelease={session.roles.some((role) =>
                    ['designer', 'engineer', 'administrator'].includes(role)
                  )}
                  canEdit={session.roles.some((role) =>
                    ['designer', 'engineer', 'reviewer', 'administrator'].includes(role)
                  )}
                />
              }
            />
            <Route
              path="*"
              element={
                <>
                  <h1>Page not found</h1>
                  <Link to="/">Return home</Link>
                </>
              }
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(Boolean(localStorage.getItem('modelryx-token')));
  useEffect(() => {
    const reset = () => setSession(null);
    window.addEventListener('modelryx-auth-required', reset);
    if (localStorage.getItem('modelryx-token'))
      api<Session>('/session')
        .then(setSession)
        .catch(() => setSession(null))
        .finally(() => setChecking(false));
    return () => window.removeEventListener('modelryx-auth-required', reset);
  }, []);
  if (checking)
    return (
      <main className="signin">
        <p role="status">Validating session…</p>
      </main>
    );
  if (!session) return <SignIn onSession={setSession} />;
  return (
    <Shell
      session={session}
      signOut={() => {
        localStorage.removeItem('modelryx-token');
        setSession(null);
      }}
    />
  );
}
createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>
);
