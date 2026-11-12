import { useEffect, useState, type Dispatch, type FormEvent, type SetStateAction } from "react";
import type { ColumnDef, ColumnFiltersState, Updater } from "@tanstack/react-table";
import type { Item } from "../../api";
import { DataTable } from "../../components/DataTable";
import type { AuditEntry, ItemNumberTemplate } from "./types";

export function ItemsPanel({ filteredItems, errors, columns, loading, numberFilter,
  nameFilter, total, offset, pageSize, filters, facets, headerFiltersAvailable, onFiltersChange, onClear,
  setNumberFilter, setNameFilter,
  onApply, onPageChange, onRefresh, onEdit, onDeleteRevision, onArchive, onRestore, onPurge }: {
  filteredItems: Item[]; errors: string[]; columns: ColumnDef<Item, any>[];
  loading: boolean; numberFilter: string; nameFilter: string;
  total: number; offset: number; pageSize: number; filters: ColumnFiltersState; facets: Record<string, string[]>; headerFiltersAvailable: boolean;
  onFiltersChange: (updater: Updater<ColumnFiltersState>) => void; onClear: () => void;
  setNumberFilter: Dispatch<SetStateAction<string>>; setNameFilter: Dispatch<SetStateAction<string>>;
  onApply: () => Promise<void>; onPageChange: (offset: number) => Promise<void>; onEdit: (item: Item) => void;
  onDeleteRevision: (item: Item) => Promise<void>;
  onArchive: (item: Item) => Promise<void>; onRestore: (item: Item) => Promise<void>;
  onPurge: (item: Item) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const itemFilterResult = { items: filteredItems, errors };
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(totalPages, Math.floor(offset / pageSize) + 1);
  const [pageInput, setPageInput] = useState(String(currentPage));
  useEffect(() => setPageInput(String(currentPage)), [currentPage]);
  const browseToPage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const requestedPage = Number.parseInt(pageInput, 10);
    if (!Number.isFinite(requestedPage)) {
      setPageInput(String(currentPage));
      return;
    }
    const page = Math.min(totalPages, Math.max(1, requestedPage));
    setPageInput(String(page));
    if (page !== currentPage) void onPageChange((page - 1) * pageSize);
  };
  return (
          <div className="admin-tab-panel">
            <div className="admin-section" style={{ display: "flex", flexDirection: "column", padding: 0, gap: "0.5rem", height: "100%" }}>
              <div className="admin-item-heading">
                <h2>Item management</h2>
                <div>
                  <button type="button" onClick={() => void onRefresh()}>Refresh</button>
                </div>
              </div>
              <div className="admin-item-filters" role="search" aria-label="Filter managed Items">
                <label>
                  Part number
                  <input
                    value={numberFilter}
                    onChange={(event) => setNumberFilter(event.target.value)}
                    placeholder="Regex, e.g. ^ASM-|100$"
                    spellCheck={false}
                  />
                </label>
                <label>
                  Name
                  <input
                    value={nameFilter}
                    onChange={(event) => setNameFilter(event.target.value)}
                    placeholder="Regex, e.g. carrier|gear"
                    spellCheck={false}
                  />
                </label>
                <div className="admin-item-filter-actions">
                  <span>{total} Items</span>
                  <button type="button" disabled={loading || errors.length > 0} onClick={() => void onApply()}>Apply filters</button>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={onClear}
                  >
                    Clear filters
                  </button>
                </div>
                {itemFilterResult.errors.length > 0 && (
                  <div className="admin-item-filter-errors" role="alert">
                    {itemFilterResult.errors.map((message) => <span key={message}>{message}</span>)}
                  </div>
                )}
              </div>
              {!loading && !headerFiltersAvailable && <p className="admin-item-filter-notice" role="status">Column filters are unavailable because the server did not provide filter options. Update and restart the Modelryx server to enable them.</p>}
              <div style={{ flex: 1, minHeight: 0, padding: "0 1.25rem 1.25rem" }}>
                <DataTable
                  columns={columns}
                  data={itemFilterResult.items}
                  getRowId={(item) => item.id}
                  initialPageSize={pageSize}
                  hidePagination
                  filterColumnIds={headerFiltersAvailable ? ["type", "cad_system", "status"] : []}
                  server={{ total, pagination: { pageIndex: Math.floor(offset / pageSize), pageSize }, onPaginationChange: () => {}, sorting: [], onSortingChange: () => {}, filters, onFiltersChange, filterOptions: facets }}
                  loading={loading}
                  emptyMessage={numberFilter || nameFilter ? "No Items match the filters" : "No Items"}
                  rowActions={(item) => [
                    { label: "Edit", onSelect: () => onEdit(item) },
                    { label: "Delete revision…", onSelect: () => void onDeleteRevision(item) },
                    item.status === "ARCHIVED" ? { label: "Restore", onSelect: () => void onRestore(item) } : { label: "Archive", onSelect: () => void onArchive(item) },
                    { label: "Permanently delete…", destructive: true, onSelect: () => void onPurge(item) },
                  ]}
                />
                <nav className="admin-item-pagination" aria-label="Item pages">
                  <button type="button" disabled={loading || currentPage === 1} onClick={() => void onPageChange(0)}>First</button>
                  <button type="button" disabled={loading || currentPage === 1} onClick={() => void onPageChange((currentPage - 2) * pageSize)}>Previous</button>
                  <form onSubmit={browseToPage}>
                    <label htmlFor="admin-item-page">Page</label>
                    <input
                      id="admin-item-page"
                      type="number"
                      min="1"
                      max={totalPages}
                      value={pageInput}
                      disabled={loading || total === 0}
                      onChange={(event) => setPageInput(event.target.value)}
                      aria-label={`Page number, 1 through ${totalPages}`}
                    />
                    <span>of {totalPages}</span>
                    <button type="submit" disabled={loading || total === 0}>Go</button>
                  </form>
                  <span className="admin-item-page-total">{total} Items</span>
                  <button type="button" disabled={loading || total === 0 || currentPage === totalPages} onClick={() => void onPageChange(currentPage * pageSize)}>Next</button>
                  <button type="button" disabled={loading || total === 0 || currentPage === totalPages} onClick={() => void onPageChange((totalPages - 1) * pageSize)}>Last</button>
                </nav>
              </div>
            </div>
          </div>
  );
}

export function NumberingPanel({ templates, loading, setShowAdd, onRefresh, onEdit, onUpdate }: {
  templates: ItemNumberTemplate[]; loading: boolean; setShowAdd: Dispatch<SetStateAction<boolean>>;
  onRefresh: () => Promise<void>; onEdit: (template: ItemNumberTemplate) => Promise<void>;
  onUpdate: (template: ItemNumberTemplate, updates: Partial<ItemNumberTemplate>) => Promise<void>;
}) {
  const numberTemplates = templates;
  return (
          <div className="admin-tab-panel">
            <div className="admin-section" style={{ padding: "1.25rem", overflow: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <h2>Automatic Item numbering</h2>
                  <p className="muted">
                    Each named template defines both Part and Assembly formats.
                    Supported fields are {"{sequence}"} and {"{year}"}, including formats such as {"{sequence:06d}"}.
                  </p>
                </div>
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button type="button" onClick={() => setShowAdd(true)}>Add</button>
                  <button type="button" onClick={() => void onRefresh()}>Refresh</button>
                </div>
              </div>
              {loading ? <p>Loading templates…</p> : (
                <div style={{ overflowX: "auto" }}>
                  <table className="admin-number-template-table">
                    <thead><tr><th>Name</th><th>Part pattern</th><th>Assembly pattern</th><th>Status</th><th>Actions</th></tr></thead>
                    <tbody>{numberTemplates.map((template) => (
                      <tr key={template.id}>
                        <td><strong>{template.name}</strong></td>
                        <td><code>{template.part_pattern}</code><small className="muted">Next: {template.part_example}</small></td>
                        <td><code>{template.assembly_pattern}</code><small className="muted">Next: {template.assembly_example}</small></td>
                        <td>{template.is_selected ? "Default" : template.is_enabled ? "Enabled" : "Disabled"}</td>
                        <td><div style={{ display: "flex", gap: "0.5rem" }}>
                        <button type="button" onClick={() => void onEdit(template)}>Edit</button>
                        {!template.is_selected && template.is_enabled && (
                          <button type="button" onClick={() => void onUpdate(template, { is_selected: true })}>
                            Select for use
                          </button>
                        )}
                        {!template.is_selected && (
                          <button type="button" onClick={() => void onUpdate(template, { is_enabled: !template.is_enabled })}>
                            {template.is_enabled ? "Disable" : "Enable"}
                          </button>
                        )}
                        </div></td>
                      </tr>
                    ))}</tbody>
                  </table>
                  {numberTemplates.length === 0 && <p>No Item-number templates configured.</p>}
                </div>
              )}
            </div>
          </div>
  );
}

export function AuditPanel({ entries, columns, loading, onRefresh }: {
  entries: AuditEntry[]; columns: ColumnDef<AuditEntry, any>[]; loading: boolean;
  onRefresh: () => Promise<void>;
}) {
  return (
          <div className="admin-tab-panel">
            <div className="admin-section" style={{ display: "flex", flexDirection: "column", padding: 0, gap: "0.5rem", height: "100%" }}>
              <div style={{ padding: "1.25rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h2>Recent audit activity</h2>
                <div style={{ flexShrink: 0 }}>
                  <button type="button" onClick={() => void onRefresh()}>Refresh</button>
                </div>
              </div>
              <div style={{ flex: 1, minHeight: 0, padding: "0 1.25rem 1.25rem" }}>
                <DataTable columns={columns} data={entries} getRowId={(entry) => entry.id} loading={loading} emptyMessage="No recent audit entries" />
              </div>
            </div>
          </div>
  );
}

