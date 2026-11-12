import type { HistoryEntry } from "./types";

export const dateTime = (value?: string | null) => value ? new Date(value).toLocaleString() : "—";

export const historyDateTime = (value: string) => new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(value)).replace(/, (?=\d{2}:)/, " ");

export const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

export const displayMetadataValue = (value: unknown): string => {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
};

export const formatPropertyName = (name: string) => name.replaceAll("_", " ")
  .replace(/\b\w/g, (character) => character.toUpperCase());

export const historyAction = (value: string) => value.replaceAll(".", " ")
  .replace(/\b\w/g, (letter) => letter.toUpperCase());

export const historyDetails = (entry: HistoryEntry) => {
  const fields = entry.details.fields;
  if (Array.isArray(fields) && fields.length) {
    return `${fields.map((field) => String(field).replaceAll("_", " ")).join(", ")} changed`;
  }
  if (entry.action === "revision.created" && entry.details.revision_code) {
    return `Revision ${entry.details.revision_code} created`;
  }
  if (["attachment.added", "attachment.replaced"].includes(entry.action) && entry.details.filename) {
    return `${entry.details.filename} · Version ${entry.details.version ?? "—"}`;
  }
  const visibleDetails = Object.entries(entry.details).filter(([key]) => key !== "item_id");
  return visibleDetails.length
    ? visibleDetails.map(([key, value]) => `${key.replaceAll("_", " ")}: ${String(value)}`).join(", ")
    : "—";
};
