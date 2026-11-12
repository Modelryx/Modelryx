import { useEffect, useRef, useState } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnSizingState,
  type PaginationState,
  type SortingState,
  type Updater,
  type VisibilityState,
} from '@tanstack/react-table';
import { Button } from './Button';

export const BLANK_FILTER_VALUE = '\u0000';
export type ServerTableState = {
  total: number;
  pagination: PaginationState;
  onPaginationChange: (updater: Updater<PaginationState>) => void;
  sorting: SortingState;
  onSortingChange: (updater: Updater<SortingState>) => void;
  filters: ColumnFiltersState;
  onFiltersChange: (updater: Updater<ColumnFiltersState>) => void;
  filterOptions: Record<string, string[]>;
};
export type RowAction<T> = {
  label: string;
  onSelect: (row: T) => void;
  disabled?: boolean;
  destructive?: boolean;
};
export type DataTableProps<T> = {
  data: T[];
  columns: ColumnDef<T, any>[];
  getRowId: (row: T, index: number) => string;
  rowActions?: (row: T) => RowAction<T>[];
  filterColumnIds?: string[];
  server?: ServerTableState;
  loading?: boolean;
  error?: string;
  emptyMessage?: string;
  pageSizeOptions?: number[];
  initialPageSize?: number;
  defaultColumnVisibility?: VisibilityState;
  hidePagination?: boolean;
  layout?: {
    visibility: VisibilityState;
    sizing: ColumnSizingState;
    onVisibilityChange: (updater: Updater<VisibilityState>) => void;
    onSizingChange: (updater: Updater<ColumnSizingState>) => void;
  };
  density?: 'comfortable' | 'compact';
};

export function DataTable<T>({
  data,
  columns,
  getRowId,
  rowActions,
  filterColumnIds = [],
  server,
  loading = false,
  error,
  emptyMessage = 'No results found.',
  pageSizeOptions = [10, 25, 50],
  initialPageSize = 10,
  defaultColumnVisibility = {},
  hidePagination = false,
  layout,
  density = 'comfortable',
}: DataTableProps<T>) {
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [filterMenu, setFilterMenu] = useState<{
    id: string;
    label: string;
    x: number;
    y: number;
    trigger: HTMLElement;
    selected: string[];
  } | null>(null);
  const filterRef = useRef<HTMLDivElement>(null);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const [visibility, setVisibility] = useState<VisibilityState>(defaultColumnVisibility);
  const [showActions, setShowActions] = useState(true);
  const [menu, setMenu] = useState<{
    row: T;
    x: number;
    y: number;
    trigger: HTMLElement;
  } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const columnPickerRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      const picker = columnPickerRef.current;
      if (picker?.open && !picker.contains(event.target as Node)) picker.open = false;
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      const picker = columnPickerRef.current;
      if (event.key === 'Escape' && picker?.open && picker.contains(document.activeElement)) {
        event.preventDefault();
        picker.open = false;
        picker.querySelector<HTMLElement>('summary')?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, []);
  const table = useReactTable({
    data,
    columns,
    getRowId,
    state: {
      sorting: server?.sorting ?? sorting,
      columnFilters: server?.filters ?? columnFilters,
      columnVisibility: layout?.visibility ?? visibility,
      columnSizing: layout?.sizing ?? columnSizing,
      ...(server ? { pagination: server.pagination } : {}),
    },
    manualPagination: !!server,
    manualSorting: !!server,
    manualFiltering: !!server,
    enableMultiSort: false,
    ...(server
      ? {
          rowCount: server.total,
          onPaginationChange: server.onPaginationChange,
        }
      : {}),
    onColumnSizingChange: layout?.onSizingChange ?? setColumnSizing,
    columnResizeMode: 'onChange',
    onColumnFiltersChange: server?.onFiltersChange ?? setColumnFilters,
    defaultColumn: {
      size: 180,
      minSize: 80,
      maxSize: 800,
      filterFn: (row, columnId, values: string[]) =>
        values.includes(String(row.getValue(columnId) ?? '—')),
    },
    onSortingChange: server?.onSortingChange ?? setSorting,
    onColumnVisibilityChange: layout?.onVisibilityChange ?? setVisibility,
    initialState: { pagination: { pageSize: initialPageSize } },
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });
  const pageCount = table.getPageCount();
  const currentPage = pageCount ? table.getState().pagination.pageIndex + 1 : 0;
  const commitPage = (input: HTMLInputElement) => {
    const value = input.value.trim();
    if (!/^\d+$/.test(value)) {
      input.value = String(currentPage);
      return;
    }
    const page = Math.min(pageCount, Math.max(1, Number(value)));
    input.value = String(page);
    table.setPageIndex(page - 1);
  };
  useEffect(() => {
    if (!menu) return;
    const panel = menuRef.current;
    if (panel) {
      panel.style.left = `${Math.max(8, Math.min(menu.x, window.innerWidth - panel.offsetWidth - 8))}px`;
      panel.style.top = `${Math.max(8, Math.min(menu.y, window.innerHeight - panel.offsetHeight - 8))}px`;
      panel.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    }
    const dismiss = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(null);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMenu(null);
        menu.trigger.focus();
      } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const items = Array.from(
          menuRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []
        );
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[
          (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
        ]?.focus();
      }
    };
    const close = () => setMenu(null);
    document.addEventListener('mousedown', dismiss);
    document.addEventListener('keydown', key);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('mousedown', dismiss);
      document.removeEventListener('keydown', key);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [menu]);
  useEffect(() => {
    if (!filterMenu) return;
    const panel = filterRef.current;
    if (panel) {
      panel.style.left = `${Math.max(8, Math.min(filterMenu.x, window.innerWidth - panel.offsetWidth - 8))}px`;
      panel.style.top = `${Math.max(8, Math.min(filterMenu.y, window.innerHeight - panel.offsetHeight - 8))}px`;
      panel.querySelector<HTMLInputElement>('input')?.focus();
    }
    const outside = (event: MouseEvent) => {
      if (!filterRef.current?.contains(event.target as Node)) setFilterMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setFilterMenu(null);
        filterMenu.trigger.focus();
      }
    };
    const close = () => setFilterMenu(null);
    const scroll = (event: Event) => {
      if (!filterRef.current?.contains(event.target as Node)) close();
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', scroll, true);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', scroll, true);
    };
  }, [filterMenu?.id]);
  const filterOptions = (id: string) =>
    Array.from(
      new Set([
        ...(server
          ? (server.filterOptions[id] ?? [])
          : table.getCoreRowModel().flatRows.map((row) => String(row.getValue(id) ?? '—'))),
        ...((table.getColumn(id)?.getFilterValue() as string[] | undefined) ?? []),
      ])
    ).sort((a, b) => a.localeCompare(b));
  const openFilter = (id: string, label: string, x: number, y: number, trigger: HTMLElement) => {
    const values = table.getColumn(id)?.getFilterValue() as string[] | undefined;
    setFilterMenu({
      id,
      label,
      x,
      y,
      trigger,
      selected: values ?? filterOptions(id),
    });
  };
  const openMenu = (row: T, x: number, y: number, trigger: HTMLElement) => {
    if (rowActions?.(row).length) setMenu({ row, x, y, trigger });
  };
  return (
    <section className="data-table" data-density={density} aria-busy={loading}>
      <div className="table-toolbar">
        <div>
          <strong>Results</strong>
          <span className="muted">
            {' '}
            {server
              ? `${server.total} total`
              : `${table.getFilteredRowModel().rows.length} of ${data.length} loaded`}
          </span>
        </div>
        <details ref={columnPickerRef} className="column-picker">
          <summary>Columns</summary>
          <div className="column-list">
            {rowActions && (
              <label>
                <input
                  type="checkbox"
                  checked={showActions}
                  onChange={(event) => setShowActions(event.target.checked)}
                />{' '}
                Actions
              </label>
            )}
            {table
              .getAllLeafColumns()
              .filter((c) => c.getCanHide())
              .map((c) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={c.getIsVisible()}
                    onChange={c.getToggleVisibilityHandler()}
                  />{' '}
                  {typeof c.columnDef.header === 'string' ? c.columnDef.header : c.id}
                </label>
              ))}
          </div>
        </details>
      </div>
      <div className="table-scroll" role="region" aria-label="Table contents" tabIndex={0}>
        <table
          className="resizable-table"
          style={{
            width: table.getTotalSize() + (rowActions && showActions ? 80 : 0),
          }}
        >
          <colgroup>
            {rowActions && showActions && <col style={{ width: 80 }} />}
            {table.getVisibleLeafColumns().map((column) => (
              <col key={column.id} style={{ width: column.getSize() }} />
            ))}
            <col />
          </colgroup>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {rowActions && showActions && (
                  <th scope="col" className="action-header">
                    Actions
                  </th>
                )}
                {group.headers.map((header) => (
                  <th
                    key={header.id}
                    scope="col"
                    onContextMenu={(event) => {
                      if (!filterColumnIds.includes(header.column.id)) return;
                      event.preventDefault();
                      const label =
                        typeof header.column.columnDef.header === 'string'
                          ? header.column.columnDef.header
                          : header.column.id;
                      openFilter(
                        header.column.id,
                        label,
                        event.clientX,
                        event.clientY,
                        event.currentTarget
                      );
                    }}
                  >
                    <div className="header-content">
                      {header.isPlaceholder ? null : header.column.getCanSort() ? (
                        <button
                          className="sort-button"
                          onClick={header.column.getToggleSortingHandler()}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}{' '}
                          <span aria-hidden="true">
                            {header.column.getIsSorted() === 'asc'
                              ? '↑'
                              : header.column.getIsSorted() === 'desc'
                                ? '↓'
                                : '↕'}
                          </span>
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                      {!header.isPlaceholder && filterColumnIds.includes(header.column.id) && (
                        <button
                          className={`header-filter${header.column.getIsFiltered() ? ' is-active' : ''}`}
                          aria-label={`Filter ${typeof header.column.columnDef.header === 'string' ? header.column.columnDef.header : header.column.id}`}
                          aria-haspopup="dialog"
                          aria-expanded={filterMenu?.id === header.column.id}
                          onClick={(event) => {
                            const rect = event.currentTarget.getBoundingClientRect();
                            openFilter(
                              header.column.id,
                              typeof header.column.columnDef.header === 'string'
                                ? header.column.columnDef.header
                                : header.column.id,
                              rect.left,
                              rect.bottom,
                              event.currentTarget
                            );
                          }}
                        >
                          ▾
                          {header.column.getIsFiltered() && (
                            <span className="sr-only"> (active)</span>
                          )}
                        </button>
                      )}
                    </div>
                    {!header.isPlaceholder && header.column.getCanResize() && (
                      <div
                        className={`column-resizer${header.column.getIsResizing() ? ' is-resizing' : ''}`}
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={`Resize ${typeof header.column.columnDef.header === 'string' ? header.column.columnDef.header : header.column.id} column`}
                        aria-valuemin={header.column.columnDef.minSize ?? 80}
                        aria-valuemax={header.column.columnDef.maxSize ?? 800}
                        aria-valuenow={header.getSize()}
                        tabIndex={0}
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                        onDoubleClick={() => header.column.resetSize()}
                        onKeyDown={(event) => {
                          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                            event.preventDefault();
                            const delta = event.key === 'ArrowRight' ? 10 : -10;
                            const min = header.column.columnDef.minSize ?? 80;
                            const max = header.column.columnDef.maxSize ?? 800;
                            const size = Math.max(min, Math.min(max, header.getSize() + delta));
                            table.setColumnSizing((current) => ({
                              ...current,
                              [header.column.id]: size,
                            }));
                          } else if (event.key === 'Home') {
                            event.preventDefault();
                            header.column.resetSize();
                          }
                        }}
                      />
                    )}
                  </th>
                ))}
                <th className="table-filler" aria-hidden="true" />
              </tr>
            ))}
          </thead>
          <tbody>
            {!loading &&
              !error &&
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onContextMenu={(event) => {
                    if (!rowActions?.(row.original).length) return;
                    event.preventDefault();
                    openMenu(row.original, event.clientX, event.clientY, event.currentTarget);
                  }}
                  tabIndex={rowActions ? 0 : undefined}
                  onKeyDown={(event) => {
                    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                      event.preventDefault();
                      const rect = event.currentTarget.getBoundingClientRect();
                      openMenu(row.original, rect.left + 20, rect.top + 20, event.currentTarget);
                    }
                  }}
                >
                  {rowActions && showActions && (
                    <td className="row-actions">
                      <Button
                        variant="ghost"
                        disabled={!rowActions(row.original).length}
                        aria-label="Row actions"
                        aria-haspopup="menu"
                        onClick={(event) => {
                          const rect = event.currentTarget.getBoundingClientRect();
                          openMenu(row.original, rect.left, rect.bottom, event.currentTarget);
                        }}
                      >
                        ⋯
                      </Button>
                    </td>
                  )}
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                  <td className="table-filler" aria-hidden="true" />
                </tr>
              ))}
          </tbody>
        </table>
        {loading ? (
          <p className="table-state" role="status">
            Loading…
          </p>
        ) : error ? (
          <p className="table-state" role="alert">
            {error}
          </p>
        ) : data.length === 0 ? (
          <p className="table-state">{emptyMessage}</p>
        ) : table.getFilteredRowModel().rows.length === 0 ? (
          <p className="table-state">No results match the selected filters.</p>
        ) : null}
      </div>
      {!hidePagination && (
        <div className="table-footer">
          <label>
            Rows per page{' '}
            <select
              value={table.getState().pagination.pageSize}
              onChange={(e) => table.setPageSize(Number(e.target.value))}
            >
              {Array.from(new Set([...pageSizeOptions, initialPageSize]))
                .sort((a, b) => a - b)
                .map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
            </select>
          </label>
          <div className="pagination">
            <label className="page-number">
              Page{' '}
              <input
                key={`${currentPage}-${pageCount}`}
                aria-label="Page number"
                type="text"
                inputMode="numeric"
                defaultValue={currentPage}
                disabled={pageCount === 0 || loading || !!error}
                onBlur={(event) => commitPage(event.currentTarget)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    event.currentTarget.blur();
                  }
                }}
              />
            </label>
            <span>of {pageCount}</span>
            <Button
              variant="secondary"
              disabled={!table.getCanPreviousPage()}
              onClick={() => table.previousPage()}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              disabled={!table.getCanNextPage()}
              onClick={() => table.nextPage()}
            >
              Next
            </Button>
          </div>
        </div>
      )}
      {filterMenu && (
        <div
          ref={filterRef}
          className="filter-menu"
          role="dialog"
          aria-label={`Filter ${filterMenu.label}`}
        >
          <strong>{filterMenu.label}</strong>
          <p>{server ? 'Filter all matching results' : 'Filter loaded results'}</p>
          <div className="filter-options">
            {filterOptions(filterMenu.id).map((value) => (
              <label key={value}>
                <input
                  type="checkbox"
                  checked={filterMenu.selected.includes(value)}
                  onChange={(event) =>
                    setFilterMenu((current) =>
                      current
                        ? {
                            ...current,
                            selected: event.target.checked
                              ? [...current.selected, value]
                              : current.selected.filter((item) => item !== value),
                          }
                        : null
                    )
                  }
                />
                {value === '—' || value === BLANK_FILTER_VALUE ? '(Blank)' : value}
              </label>
            ))}
          </div>
          {filterOptions(filterMenu.id).length === 0 && <p>No matching values.</p>}
          <div className="filter-controls">
            <Button
              variant="ghost"
              onClick={() =>
                setFilterMenu((current) =>
                  current ? { ...current, selected: filterOptions(current.id) } : null
                )
              }
            >
              Select all
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setFilterMenu((current) => (current ? { ...current, selected: [] } : null))
              }
            >
              Select none
            </Button>
          </div>
          <div className="filter-controls">
            <Button
              variant="secondary"
              onClick={() => {
                table.getColumn(filterMenu.id)?.setFilterValue(undefined);
                table.setPageIndex(0);
                setFilterMenu(null);
                filterMenu.trigger.focus();
              }}
            >
              Clear
            </Button>
            <Button
              onClick={() => {
                const options = filterOptions(filterMenu.id);
                table
                  .getColumn(filterMenu.id)
                  ?.setFilterValue(
                    filterMenu.selected.length === options.length ? undefined : filterMenu.selected
                  );
                table.setPageIndex(0);
                setFilterMenu(null);
                filterMenu.trigger.focus();
              }}
            >
              Apply
            </Button>
          </div>
        </div>
      )}
      {menu && (
        <div ref={menuRef} className="row-menu" role="menu" aria-label="Row actions">
          {rowActions?.(menu.row).map((action, index) => (
            <button
              key={`${action.label}-${index}`}
              role="menuitem"
              disabled={action.disabled}
              className={action.destructive ? 'destructive' : ''}
              onClick={() => {
                setMenu(null);
                action.onSelect(menu.row);
                menu.trigger.focus();
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
