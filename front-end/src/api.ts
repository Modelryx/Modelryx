import { serverApiUrl } from './workflow-api';

export type Session = {
  subject: string;
  display_name: string;
  role: string;
  roles: string[];
};
export type Item = {
  id: string;
  item_number: string;
  name: string;
  item_type: string;
  status: string;
  cad_system?: string | null;
  category?: string | null;
  description?: string | null;
  current_revision_id?: string | null;
  current_revision_code?: string | null;
  workspace_revision_id?: string | null;
  workspace_revision_code?: string | null;
  checkout_scope?: string | null;
};
export type Revision = {
  id: string;
  revision_code: string;
  cad_system: string;
  native_format: string;
  state: string;
  lifecycle_state: string;
  approval_status?: string | null;
  checkout_status: string;
  created_at: string;
  metadata_json?: Record<string, unknown> | null;
};
export type Attachment = {
  id: string;
  name: string;
  attachment_type: string;
  lifecycle_state: string;
  latest_ready_version: {
    version_number: number;
    filename: string;
    download_url: string | null;
  } | null;
  versions: { version_number: number; state: string }[];
};
export type RevisionArtifact = {
  relation_id: string;
  relation_type: string;
  artifact: {
    id: string;
    name: string;
    artifact_type: string;
    classification: string;
    source: string;
    generator: string | null;
    derived_from_artifact_id: string | null;
    state: string;
    files: {
      id: string;
      original_filename: string;
      sha256: string;
      role: string | null;
      reference_name: string | null;
      byte_size: number;
      state: string;
      download_url: string | null;
    }[];
  };
};
export type RevisionMetadata = {
  metadata_status: string;
  current: {
    status: string;
    metadata: Record<string, unknown>;
    derived: Record<string, unknown>;
    warnings: unknown[];
    errors: unknown[];
  };
};
export type SearchRow = {
  item_id: string;
  revision_id?: string | null;
  item_number: string;
  name: string;
  item_type: string;
  revision_code?: string | null;
  cad_system?: string | null;
  item_status?: string | null;
};
export type SearchResponse = {
  results: SearchRow[];
  total: number;
  warnings: string[];
  facets: {
    item_types: (string | null)[];
    cad_systems: (string | null)[];
    item_statuses: (string | null)[];
  } | null;
};
export type AssistantReference = {
  type: 'ITEM' | 'REVISION' | 'METADATA' | 'ASSEMBLY' | 'SEARCH_RESULT' | 'VALIDATION';
  item_id: string;
  revision_id: string | null;
  item_number: string;
  revision_code: string | null;
  label: string;
};
export type AssistantNavigationAction =
  | { type: 'OPEN_ITEM'; item_id: string; revision_id: string | null }
  | { type: 'OPEN_REVISION'; item_id: string; revision_id: string | null };
export type AssistantComparisonAction = {
  type: 'OPEN_COMPARISON';
  item_id: string;
  from_revision_id: string;
  to_revision_id: string;
  comparison_id: string;
};
export type AssistantViewerAction = {
  type: 'HIGHLIGHT_OCCURRENCES' | 'ISOLATE_OCCURRENCES';
  occurrence_ids: string[];
};
export type AssistantViewerCommand = AssistantViewerAction & { id: string };
export type AssistantAction =
  AssistantNavigationAction | AssistantComparisonAction | AssistantViewerAction;
export type AssistantMessageResponse = {
  conversation_id: string;
  message_id: string;
  answer: string;
  intent: string;
  references: AssistantReference[];
  actions: AssistantAction[];
  warnings: string[];
  trace_id: string;
};
export type Upload = {
  id: string;
  status: string;
  operation: string;
  item_number: string | null;
  item_name: string | null;
  primary_filename: string | null;
  workflow_state: string | null;
  progress_percent: number;
  is_finished: boolean;
  is_archived: boolean;
  item_id: string | null;
  revision_id: string | null;
  can_edit: boolean;
  can_resume: boolean;
  can_cancel: boolean;
  download_url: string | null;
};
const server = () => localStorage.getItem('modelryx-server')?.replace(/\/$/, '') || '';
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const token = localStorage.getItem('modelryx-token');
  const response = await fetch(serverApiUrl(path), {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      localStorage.removeItem('modelryx-token');
      window.dispatchEvent(new Event('modelryx-auth-required'));
    }
    throw new Error(
      typeof result.detail === 'string' ? result.detail : `Request failed (${response.status})`
    );
  }
  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>);
}
export async function login(url: string, username: string, password: string) {
  let response: Response;
  try {
    response = await fetch(`${url.replace(/\/$/, '')}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new Error(
      `Cannot connect to ${url}. Check the server URL, network connection, and allowed frontend origin.`
    );
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof result.detail === 'string' ? result.detail : `Sign in failed (${response.status})`
    );
  return result as { access_token: string };
}

export async function downloadManagedFile(path: string, filename: string): Promise<void> {
  const base = new URL(server());
  const url = new URL(path, base);
  if (url.origin !== base.origin || !url.pathname.startsWith('/api/v1/')) {
    throw new Error('The server returned an unsupported download address.');
  }
  const token = localStorage.getItem('modelryx-token');
  const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('modelryx-token');
      window.dispatchEvent(new Event('modelryx-auth-required'));
    }
    throw new Error(`Download failed (${response.status})`);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export async function fetchPreviewBlob(path: string, signal: AbortSignal): Promise<Blob> {
  const base = new URL(server());
  const apiPath = path.startsWith('/api/v1/') ? path : `/api/v1${path}`;
  const url = new URL(apiPath, base);
  if (url.origin !== base.origin || !url.pathname.startsWith('/api/v1/revisions/')) {
    throw new Error('The server returned an unsupported preview address.');
  }
  const token = localStorage.getItem('modelryx-token');
  const response = await fetch(url, {
    signal,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem('modelryx-token');
      window.dispatchEvent(new Event('modelryx-auth-required'));
    }
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === 'string' ? body.detail : `Preview request failed (${response.status})`
    );
  }
  return response.blob();
}
