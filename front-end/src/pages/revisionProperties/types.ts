import type { Item } from "../../workflow-api";
import type { StageProcessingJob } from "../Stages";

export type Revision = {
  id: string; revision_code: string; description: string | null; cad_system: string;
  native_format: string; metadata_json: Record<string, unknown>; state: string;
  lifecycle_state: "WORKING" | "RELEASED" | "SUPERSEDED" | "OBSOLETE";
  released_by: string | null; released_at: string | null; created_at: string;
  committed_at: string | null;
  checkout_status: "AVAILABLE" | "CHECKED_OUT_BY_ME" | "CHECKED_OUT_BY_OTHER";
  approval_status: "PENDING" | "APPROVED" | "REJECTED" | "WITHDRAWN" | "STALE" | "EXPIRED" | null;
  approval_request_id: string | null; approval_snapshot_id: string | null;
};

export type ContentSnapshot = {
  id: string; sequence: number; commit_id: string; comment: string | null;
  created_at: string; artifact_count: number;
};

export type RevisionFile = {
  id: string; artifact_id: string; reference_name: string | null; role: string;
  relation_type: string; artifact_type: string; classification: string; source: string;
  generator: string | null; item_id: string; item_number: string; item_name: string;
  revision_id: string; revision_code: string; kind: string; filename: string;
  mime_type: string; byte_size: number; state: string; download_url: string;
  tree_depth?: number; tree_node_key?: string; tree_has_children?: boolean;
};

export type PreviewRepresentation = {
  id: string; kind: "3D" | "2D"; filename: string; media_type: string;
  preview_url: string | null; source_artifact_id?: string | null; display_name?: string;
  source_path?: string; sheet_count?: number;
  sheets?: Array<{ id: string; name: string; page: number; width_mm: number | null;
    height_mm: number | null; view_count: number; title_block_name: string | null }>;
  warnings?: string[]; state?: "READY" | "FAILED"; error?: string; retry_url?: string | null;
};

export type RevisionArtifact = {
  relation_id: string; relation_type: string; sequence: number; is_primary: boolean;
  artifact: { id: string; artifact_type: string; name: string; classification: string;
    source: string; generator: string | null; state: string;
    files: Array<{ id: string; kind: string; reference_name: string | null;
      original_filename: string; mime_type: string; byte_size: number; state: string;
      role: string | null; download_url: string }> };
};

export type RevisionMetadata = {
  revision_id: string; metadata_status: string;
  current: { id: string; status: string; metadata: Record<string, unknown>;
    derived: Record<string, unknown>; warnings: unknown[]; errors: unknown[];
    raw_metadata_url: string | null };
};

export type ProcessingJob = StageProcessingJob;
export type AttachmentVersion = {
  id: string; version_number: number; state: string; filename: string; mime_type: string;
  byte_size: number; sha256: string; scope: "ITEM" | "REVISION"; revision_id: string | null;
  created_at: string; ready_at: string | null; upload_url: string | null; download_url: string | null;
};
export type Attachment = {
  id: string; name: string; attachment_type: string; description: string | null;
  latest_ready_version: AttachmentVersion | null; versions: AttachmentVersion[];
};

export type BomOccurrence = {
  id: string; occurrence_key: string; source_reference: string;
  reference_designator: string | null; quantity: number; is_suppressed?: boolean;
  is_visible?: boolean;
  properties?: { bom_structure?: string; excluded?: boolean; virtual?: boolean;
    substitute?: boolean; reference?: boolean; model_state?: string; member_type?: string;
    member_name?: string; factory_document_path?: string;
    native_bom_rows?: Array<{ view_type?: string; item_number?: string;
      total_quantity?: string; merged?: boolean; promoted?: boolean; rolled_up?: boolean }> };
  child_item: Item; child_revision: Revision; children: BomOccurrence[];
};

export type NativeBomRow = {
  id: string; item_number: string; item_quantity: number; total_quantity: string;
  bom_structure: string; merged: boolean; promoted: boolean; rolled_up: boolean;
  occurrence_names: string[]; definition_paths: string[]; children: NativeBomRow[];
};
export type NativeBomResponse = {
  revision_id: string; active_model_state: string | null; active_iassembly_member: string | null;
  selection_reason: string | null; views: Array<{ name: string; view_type: string;
    model_state_member_name: string | null; iassembly_member_name: string | null;
    rows: NativeBomRow[] }>;
};
export type BomResponse = { assembly_item: Item; assembly_revision: Revision; occurrences: BomOccurrence[] };

export type Relationship = {
  id: string; relationship_type: string; direction: "reference" | "referenced_by";
  item_id: string; item_number: string; item_name: string; revision_id: string | null;
  revision_code: string | null;
  occurrence_count?: number;
};
export type HistoryEntry = {
  id: string; action: string; changed_by: string; details: Record<string, unknown>;
  occurred_at: string;
};
export type WhereUsedPathSegment = {
  occurrence_id: string; occurrence_key: string; quantity: number; parent_item_id: string;
  parent_revision_id: string; child_item_id: string; child_revision_id: string;
};
export type WhereUsedEntry = {
  occurrence_id: string; occurrence_key: string; parent_item: Item; parent_revision: Revision;
  direct: boolean; depth: number; total_quantity: number; path: WhereUsedPathSegment[];
  occurrence_count?: number; paths?: WhereUsedPathSegment[][];
};
export type WhereUsedResponse = { revision_id: string; items: WhereUsedEntry[]; warnings: string[] };

export type CADSession = {
  id: string; revision_id: string; status: string; worker_id: string; created_at: string;
  ready_at: string | null; expires_at: string; error: Record<string, unknown>;
};
export type CADWorkerSummary = {
  worker_id: string; cad_system: string; worker_version: string; protocol_version: string;
  available: boolean; mutation_enabled: boolean;
};
export type CADInspection = {
  document_label: string | null; material: string | null; mutation_ready: boolean;
  warnings: string[]; features: Array<{ id: string; name: string | null; type: string;
    parameters: Array<{ name: string; value: string | number | boolean | null;
      unit: string | null; editable: boolean }> }>;
};
export type CADPlan = {
  id: string; status: string; operation: string; target_id: string; parameter: string;
  expected_value: unknown; new_value: unknown; unit: string | null;
  preview: Record<string, unknown>; expires_at: string;
};
export type CADExecution = { id: string; status: string; error: Record<string, unknown> };

export type RevisionSection = "overview" | "versions" | "structure" | "relationships"
  | "files" | "properties" | "preview" | "cad" | "stages" | "intelligence" | "history";
