export type Revision = {
  id: string;
  revision_code: string;
  cad_system: string;
  native_format: string;
  metadata_json: Record<string, unknown>;
  derived_metadata_json: Record<string, unknown> | null;
  state: string;
  created_at: string;
  committed_at: string | null;
  lifecycle_state: "WORKING" | "RELEASED" | "SUPERSEDED" | "OBSOLETE";
  released_by: string | null;
  released_at: string | null;
  checkout_status: "AVAILABLE" | "CHECKED_OUT_BY_ME" | "CHECKED_OUT_BY_OTHER";
};

export type HistoryEntry = {
  id: string;
  action: string;
  changed_by: string;
  details: Record<string, unknown>;
  occurred_at: string;
};

export type CADSessionSummary = {
  id: string;
  revision_id: string;
  status: string;
};

export type AdditionalFileVersion = {
  id: string; version_number: number; state: string; filename: string; mime_type: string;
  byte_size: number; sha256: string; created_at: string; ready_at: string | null;
  download_url: string | null;
};

export type AdditionalFile = {
  id: string; name: string; attachment_type: string; description: string | null;
  lifecycle_state: "ACTIVE" | "ARCHIVED"; latest_ready_version: AdditionalFileVersion | null;
  versions: AdditionalFileVersion[];
  checkout_status: "AVAILABLE" | "CHECKED_OUT_BY_ME" | "CHECKED_OUT_BY_OTHER";
};

