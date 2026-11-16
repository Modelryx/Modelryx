import type { Item } from '../../api';

export type User = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  roles: string[];
  is_active: boolean;
  failed_login_attempts: number;
  locked_until: string | null;
  last_login_at: string | null;
  password_changed_at: string | null;
  created_at: string;
};

export type UserActivity = {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  occurred_at: string;
  details: Record<string, unknown>;
};

export type ApprovalGroup = {
  id: string;
  name: string;
  is_active: boolean;
  member_ids: string[];
};

export type Status = {
  status: string;
  queue_depth: number;
  active_staging_sessions: number;
  verified_artifact_bytes: number;
  storage_free_bytes: number;
  processing_jobs: Record<string, number>;
  revisions: Record<string, number>;
  checkout_notification_health: 'HEALTHY' | 'WARNING' | 'DEGRADED';
  checkout_notification_consecutive_failures: number;
  checkout_notification_last_attempt_at: string | null;
  checkout_notification_last_success_at: string | null;
  checkout_notification_last_examined: number;
  checkout_notification_last_created: number;
  workers: WorkerStatus[];
};

export type WorkerStatus = {
  worker_type: 'FREECAD' | 'STEP';
  availability: 'ONLINE' | 'OFFLINE';
  machine_name: string | null;
  worker_version: string | null;
  application_version: string | null;
  adapter_version: string | null;
  compatibility_state: 'COMPATIBLE' | 'INCOMPATIBLE' | 'UNKNOWN';
  capabilities: Record<string, boolean | string | number | null>;
  status: 'IDLE' | 'BUSY' | 'DEGRADED' | null;
  current_job: boolean;
  detail: string | null;
  last_heartbeat_at: string | null;
  configured_url: string | null;
};

export type CadWorkerSettings = {
  freecad_url: string | null;
  freecad_analyzer_url: string | null;
  autocad_url: string | null;
  inventor_url: string | null;
  nx_url: string | null;
};

export type CadWorkerConnectionStatus = {
  cad_system: 'autocad' | 'inventor' | 'siemens_nx' | 'freecad' | 'freecad_analyzer';
  url: string | null;
  availability: 'ONLINE' | 'OFFLINE' | 'NOT_CONFIGURED';
  configuration_source:
    'ADMIN_OVERRIDE' | 'ENVIRONMENT_DEFAULT' | 'ADMIN_DISABLED' | 'NOT_CONFIGURED';
  checked_at: string;
  detail: string | null;
  capabilities: Record<string, unknown>;
  processing_readiness: 'READY' | 'NOT_READY' | 'UNKNOWN' | 'NOT_APPLICABLE';
  readiness_code: string | null;
  readiness_detail: string | null;
};

export type CadWorkerStatus = {
  freecad: CadWorkerConnectionStatus;
  freecad_analyzer: CadWorkerConnectionStatus;
  autocad: CadWorkerConnectionStatus;
  inventor: CadWorkerConnectionStatus;
  nx: CadWorkerConnectionStatus;
};

export type AuditEntry = {
  id: string;
  action: string;
  entity_type: string;
  occurred_at: string;
  details: Record<string, unknown>;
};

export type ItemPurgePreview = {
  item_id: string;
  item_number: string;
  name: string;
  revision_count: number;
  artifact_count: number;
  stored_bytes: number;
  attachment_count: number;
  blocking_reference_count: number;
  blocking_item_numbers?: string[];
  cascade_item_numbers: string[];
  protected_legacy_item_numbers: string[];
  external_reference_item_numbers: string[];
};

export type ItemPurgeDialog = {
  item: Item;
  preview: ItemPurgePreview;
  selectedItemNumbers: string[];
  confirmation: string;
};

export type RevisionDeletionOption = {
  id: string;
  revision_code: string;
  state: string;
  lifecycle_state: string;
  created_at: string;
  is_current: boolean;
  eligible: boolean;
  reason: string | null;
};

export type RevisionDeletionDialog = {
  item: Item;
  revisions: RevisionDeletionOption[];
  selectedRevisionId: string;
  confirmation: string;
};

export type CheckoutNotificationRun = {
  examined: number;
  created: number;
  warning_seconds: number;
};

export type ItemNumberTemplate = {
  id: string;
  name: string;
  part_pattern: string;
  assembly_pattern: string;
  part_next_value: number;
  assembly_next_value: number;
  is_enabled: boolean;
  is_selected: boolean;
  part_example: string;
  assembly_example: string;
};
