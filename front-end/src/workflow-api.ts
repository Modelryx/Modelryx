export type Item = {
  id: string;
  item_number: string;
  name: string;
  item_type: string;
  category?: string | null;
  status: string;
  checkout_status?: string;
  current_revision_id?: string | null;
  current_revision_code?: string | null;
  cad_system?: string | null;
  workspace_revision_id?: string | null;
  workspace_revision_code?: string | null;
  checkout_acquired_at?: string | null;
  checkout_expires_at?: string | null;
  checkout_scope?: string | null;
  description?: string | null;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
};
export type SearchEntry = {
  item: Item;
  current_revision: { id: string; revision_code: string; state: string; cad_system: string } | null;
};
export type UserNotification = {
  id: string;
  kind: string;
  title: string;
  message: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
};
export type ApprovalRequest = {
  id: string;
  revision_id: string;
  snapshot_id: string;
  reviewer_group_id: string | null;
  reviewer_group_name: string | null;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'WITHDRAWN' | 'STALE' | 'EXPIRED';
  policy_version: string;
  policy: Record<string, unknown>;
  required_approvals: number;
  approval_count: number;
  due_at: string;
  request_comment: string | null;
  requested_by: string;
  requested_at: string;
  decision_comment: string | null;
  warnings_acknowledged: boolean;
  decided_by: string | null;
  decided_at: string | null;
  can_withdraw: boolean;
  can_decide: boolean;
};
export type ReviewInboxEntry = {
  approval: ApprovalRequest;
  item_id: string;
  item_number: string;
  item_name: string;
  revision_code: string;
  revision_state: string;
  snapshot_sequence: number;
};
export type ApprovalGroupOption = {
  id: string;
  name: string;
  eligible_member_count: number;
};

export type ComparisonChange = {
  id: string;
  change_key: string;
  change_type: string;
  property_path: string;
  old_value: unknown;
  new_value: unknown;
  unit: string | null;
  delta: number | null;
  percent_change: number | null;
  significance: string;
  categories: string[];
  confidence: number;
  provenance: string;
  metadata: Record<string, unknown>;
};
export type ComparisonDomain = {
  id: string;
  domain: string;
  status: string;
  engine_version: string;
  result: Record<string, unknown>;
  result_hash: string;
  change_count: number;
  warnings: string[];
  error_detail: string | null;
  changes: ComparisonChange[];
};
export type RevisionComparison = {
  id: string;
  item_id: string;
  from_revision_id: string;
  to_revision_id: string;
  status: string;
  comparison_engine_version: string;
  metadata_schema_version: string;
  rules_version: string;
  requested_domains: string[];
  summary: Record<string, unknown>;
  error_detail: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  domains: ComparisonDomain[];
};
export type ComparisonExplanation = {
  id: string;
  comparison_id: string;
  comparison_result_hash: string;
  level: 'QUICK' | 'ENGINEERING' | 'DETAILED';
  mode: 'AI' | 'DETERMINISTIC';
  summary: string;
  facts: { text: string; change_keys: string[] }[];
  cause: { text: string; source_ids: string[]; recorded: boolean };
  advisory: string[];
  cited_change_keys: string[];
  cited_source_ids: string[];
  warnings: string[];
  generated_at: string;
};

export type ValidationFinding = {
  id: string;
  code: string;
  category: string;
  severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  blocking: boolean;
  state: string;
  title: string;
  message: string;
  source: Record<string, unknown>;
  location: Record<string, unknown>;
  details: Record<string, unknown>;
  evidence: Record<string, unknown>;
  actions: string[];
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  acknowledgement_comment: string | null;
};
export type ValidationReport = {
  validation_id: string;
  staging_id: string;
  run_number: number;
  snapshot_version: number;
  status: 'READY' | 'READY_WITH_WARNINGS' | 'BLOCKED' | 'ERROR';
  report_hash: string;
  policy_version: string;
  summary: Record<'critical' | 'error' | 'warning' | 'info' | 'blocking' | 'total', number>;
  validator_states: Record<string, 'PASS' | 'FAIL' | 'WARNING' | 'NOT_EVALUATED'>;
  findings: ValidationFinding[];
};
export type ValidationAnalysis = {
  id: string;
  validation_id: string;
  mode: 'AI' | 'DETERMINISTIC';
  model: string | null;
  prompt_version: string;
  output: {
    headline: string;
    summary: string;
    issues: Array<{
      finding_id: string;
      explanation: string;
      impact: string;
      recommended_actions: Array<{ action: string; label: string }>;
    }>;
  };
  warnings: string[];
};

export type AIActionProposal = {
  id: string;
  tool_name: string;
  tool_version: string;
  risk: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  status: 'PENDING' | 'CONFIRMED' | 'EXECUTED' | 'CANCELLED' | 'EXPIRED' | 'STALE' | 'FAILED';
  arguments: Record<string, unknown>;
  preview: {
    title?: string;
    summary?: string;
    target?: string;
    persistent_change?: string;
    confirmation_reason?: string;
  };
  target_type: string;
  target_id: string;
  requested_by: string;
  confirmed_by: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
};
export type AIActionExecution = {
  id: string;
  proposal_id: string;
  idempotency_key: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  result: { validation_id?: string; status?: string; summary?: Record<string, number> };
  error: { code?: string; message?: string };
  executed_by: string;
  started_at: string;
  completed_at: string | null;
};

export type BOMSummary = {
  snapshot_id: string;
  unique_component_revisions: number;
  total_occurrences: number;
  total_quantity: number;
  subassemblies: number;
  lifecycle: Record<string, number>;
  documentation: { with_drawing: number; without_drawing: number; coverage_percent: number };
  reuse: { reused_components: number; single_use_components: number };
  materials: Record<string, number>;
  material_coverage: {
    covered_quantity: number;
    missing_quantity: number;
    coverage_percent: number;
  };
  mass: {
    total_mass_kg: number;
    covered_quantity: number;
    missing_quantity: number;
    coverage_percent: number;
  };
};
export type BOMNode = {
  occurrence_id: string;
  occurrence_path: string;
  item_id: string;
  item_number: string;
  item_name: string;
  item_type: string;
  revision_id: string;
  revision: string;
  lifecycle_status: string;
  quantity: number;
  effective_quantity: number;
  suppressed: boolean;
  has_drawing: boolean;
  material: string | null;
  mass_kg: number | null;
  metadata_status: string | null;
};
export type BOMAIResponse = {
  plan: Record<string, unknown>;
  result: {
    total: number;
    returned: number;
    truncated: boolean;
    nodes: BOMNode[];
    summary: BOMSummary;
  };
  mode: 'AI' | 'DETERMINISTIC';
  answer: string;
  evidence_occurrence_ids: string[];
  warnings: string[];
};
export type StandardizationCandidate = {
  revision_a_id: string;
  revision_b_id: string;
  item_a: string;
  item_b: string;
  score: number;
  score_coverage: number;
  classification: string;
  signals: Record<string, number | null>;
  reasons: string[];
  differences: string[];
  limitations: string[];
  advisory: string;
};
export type StandardizationResponse = {
  snapshot_id: string;
  rules_version: string;
  total: number;
  returned: number;
  truncated: boolean;
  candidates: StandardizationCandidate[];
};
export type BOMDuplicateResponse = {
  snapshot_id: string;
  total: number;
  pairs: Array<{
    revision_a_id: string;
    revision_b_id: string;
    item_a: string;
    item_b: string;
    classification: string;
    score: number | null;
    reasons: string[];
    differences: string[];
    advisory: string;
  }>;
};
export type BOMReadinessResponse = {
  snapshot_id: string;
  status: 'READY' | 'READY_WITH_WARNINGS' | 'BLOCKED';
  findings: Array<{
    code: string;
    severity: 'WARNING' | 'BLOCKING';
    count: number;
    occurrence_ids: string[];
    message: string;
  }>;
  validation_coverage: 'UNAVAILABLE';
  limitations: string[];
};

export type NumericPredicate = {
  eq?: number | null;
  lt?: number | null;
  lte?: number | null;
  gt?: number | null;
  gte?: number | null;
  target?: number | null;
  tolerance?: number | null;
};
export type EngineeringSearchRequest = {
  version: '1';
  entity_type: 'item' | 'part' | 'assembly';
  text: { terms: string[] };
  filters: {
    identifiers: string[];
    item_number_regex?: string | null;
    name_regex?: string | null;
    classification?: string | null;
    material?: {
      family: string;
      grade?: string | null;
      temper?: string | null;
      raw?: string | null;
    } | null;
    material_presence?: 'specified' | 'missing' | null;
    feature_filters: Array<{
      feature:
        | 'hole'
        | 'fillet'
        | 'chamfer'
        | 'extrusion'
        | 'revolution'
        | 'pocket'
        | 'pattern'
        | 'thread';
      presence: 'present' | 'absent';
    }>;
    geometry?: Record<string, NumericPredicate | null> | null;
    mass_kg?: NumericPredicate | null;
    cad_system?: string | null;
    artifact_format?: string | null;
    revision?: string | null;
    status?: string | null;
    lifecycle?: 'WORKING' | 'RELEASED' | 'SUPERSEDED' | 'OBSOLETE' | null;
    owner?: string | null;
    created?: { after?: string | null; before?: string | null } | null;
    modified?: { after?: string | null; before?: string | null } | null;
    approval_state?: 'PENDING' | 'APPROVED' | 'REJECTED' | null;
    approval_assigned_to_me?: boolean | null;
    approval_due_before?: string | null;
    checkout_owner?: string | null;
    artifact_presence?: 'drawing' | 'preview' | 'native_cad' | null;
    artifact_missing?: boolean | null;
    extraction_status?: 'available' | 'missing' | 'failed' | null;
    has_warnings?: boolean | null;
  };
  relationships: {
    type: 'contains' | 'where_used' | 'parent_assembly';
    target_item_id: string;
    depth: 'direct' | 'recursive';
    max_depth: number;
  }[];
  similarity: {
    source_item_revision_id: string;
    method: 'metadata_embedding';
    minimum_score: number;
  } | null;
  sort: { field: string; direction: 'asc' | 'desc' }[];
  limit: number;
  offset: number;
};
export type EngineeringSearchResult = {
  item_id: string;
  revision_id?: string | null;
  item_number: string;
  name: string;
  item_type: string;
  revision_code?: string | null;
  cad_system?: string | null;
  material?: string | null;
  dimensions_mm?: number[] | null;
  mass_kg?: number | null;
  score: number;
  description?: string | null;
  category?: string | null;
  item_status?: string | null;
  metadata_status?: string | null;
  volume_mm3?: number | null;
  surface_area_mm2?: number | null;
  solid_count?: number | null;
  face_count?: number | null;
  edge_count?: number | null;
  hole_count?: number | null;
  fillet_count?: number | null;
  chamfer_count?: number | null;
  extrusion_count?: number | null;
  revolution_count?: number | null;
  pocket_count?: number | null;
  pattern_count?: number | null;
  thread_count?: number | null;
  scores: { text: number; metadata: number; vector: number; relationship: number; final: number };
  match_reasons: { type: string; query: string; actual?: string | number | null }[];
};
export type EngineeringSearchResponse = {
  interpretation: EngineeringSearchRequest;
  results: EngineeringSearchResult[];
  total: number;
  warnings: string[];
  confidence?: number | null;
  coverage: { matched: number; excluded_unknown: number; coverage_rules: string[] };
  trace: { structured_matches: number; returned: number; parser: string; duration_ms: number };
  session_id?: string | null;
};

export type AssistantReference = {
  type: 'ITEM' | 'REVISION' | 'METADATA' | 'ASSEMBLY' | 'SEARCH_RESULT' | 'VALIDATION';
  item_id: string;
  revision_id?: string | null;
  item_number: string;
  revision_code?: string | null;
  label: string;
};
export type AssistantNavigationAction = {
  type: 'OPEN_ITEM' | 'OPEN_REVISION' | 'OPEN_COMPARISON';
  item_id: string;
  revision_id?: string | null;
  from_revision_id?: string | null;
  to_revision_id?: string | null;
  comparison_id?: string | null;
};
export type AssistantViewerAction = {
  type: 'HIGHLIGHT_OCCURRENCES' | 'ISOLATE_OCCURRENCES';
  occurrence_ids: string[];
};
export type AssistantAction = AssistantNavigationAction | AssistantViewerAction;
export type AssistantMessageResponse = {
  conversation_id: string;
  message_id: string;
  answer: string;
  intent:
    | 'ITEM_SUMMARY'
    | 'ITEM_METADATA'
    | 'REVISION_HISTORY'
    | 'COMPARE_REVISIONS'
    | 'BOM_QUERY'
    | 'WHERE_USED'
    | 'SELECTED_COMPONENT'
    | 'ENGINEERING_SEARCH'
    | 'SIMILAR_PART_SEARCH'
    | 'VALIDATION_DIAGNOSIS'
    | 'VIEWER_HIGHLIGHT'
    | 'VIEWER_ISOLATE'
    | 'VIEWER_MODIFIED_COMPONENTS'
    | 'SELECTED_WHERE_USED';
  references: AssistantReference[];
  actions: AssistantAction[];
  warnings: string[];
  trace_id: string;
};

const token = () => localStorage.getItem('modelryx-token');
const serverUrl = () => localStorage.getItem('modelryx-server') ?? '';

export function serverApiUrl(path: string): string {
  const server = serverUrl().replace(/\/+$/, '');
  const base = new URL(server || window.location.origin);
  const apiPath =
    path === '/api/v1' || path.startsWith('/api/v1/')
      ? path
      : `/api/v1${path.startsWith('/') ? path : `/${path}`}`;
  const url = /^https?:\/\//i.test(path) ? path : `${server}${apiPath}`;
  const resolved = new URL(url, base);
  const apiPrefix = `${base.pathname.replace(/\/+$/, '')}/api/v1`;
  if (
    resolved.origin !== base.origin ||
    resolved.username ||
    resolved.password ||
    (resolved.pathname !== apiPrefix && !resolved.pathname.startsWith(`${apiPrefix}/`))
  ) {
    throw new Error('The server returned an unsupported API address.');
  }
  return url;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function validationMessage(detail: unknown): string | undefined {
  if (!Array.isArray(detail)) return undefined;
  const messages = detail.flatMap((issue) => {
    if (!issue || typeof issue.msg !== 'string') return [];
    const path = Array.isArray(issue.loc)
      ? issue.loc
          .filter((part: unknown) => part !== 'body' && part !== 'query' && part !== 'path')
          .join(' · ')
          .replaceAll('_', ' ')
      : '';
    const label = path ? path[0].toUpperCase() + path.slice(1) : '';
    return [label ? `${label}: ${issue.msg}` : issue.msg];
  });
  return messages.length ? messages.join('; ') : undefined;
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const url = serverApiUrl(path);

  const response = await fetch(url, {
    method,
    headers: {
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      localStorage.removeItem('modelryx-token');
      window.dispatchEvent(new Event('modelryx-auth-required'));
    }
    const detail = data.detail;
    throw new ApiError(
      typeof detail === 'string'
        ? detail
        : detail && typeof detail === 'object' && typeof detail.message === 'string'
          ? detail.message
          : (validationMessage(detail) ?? `Request failed (${response.status})`),
      response.status,
      detail
    );
  }

  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>);
}

export type LoginResult = {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  display_name: string;
  role: string;
  roles: string[];
};

export type Session = {
  subject: string;
  display_name: string;
  role: string;
  roles: string[];
};

export async function login(
  server: string,
  username: string,
  password: string
): Promise<LoginResult> {
  const base = server.replace(/\/$/, '');
  const response = await fetch(`${base}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof data.detail === 'string' ? data.detail : `Login failed (${response.status})`
    );
  }
  return data as LoginResult;
}

export function authorizationHeader(): Record<string, string> {
  const value = token();
  return value ? { Authorization: `Bearer ${value}` } : {};
}
