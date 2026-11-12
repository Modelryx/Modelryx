import { type ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  api,
  type EngineeringSearchRequest,
  type EngineeringSearchResponse,
  type EngineeringSearchResult,
  type NumericPredicate,
} from "../workflow-api";
import { DataTable } from "../components/DataTable";

type Chip = { key: string; label: string };
type LibraryUser = { id: string; username: string; display_name: string };
type LibraryFilterSelection = {
  itemType: "item" | "part" | "assembly";
  itemNumberPattern: string;
  namePattern: string;
  cadSystem: string;
  createdBy: string;
  checkedOutBy: string;
};

const libraryFilterDefaults: LibraryFilterSelection = {
  itemType: "item",
  itemNumberPattern: "",
  namePattern: "",
  cadSystem: "",
  createdBy: "",
  checkedOutBy: "",
};
const libraryCadSystems = [
  "STEP", "FreeCAD", "AutoCAD", "Inventor", "SOLIDWORKS", "Fusion", "Creo", "CATIA", "NX",
];

function libraryFilterStorageKey(): string {
  const username = localStorage.getItem("modelryx-username")?.trim() || "anonymous";
  return `modelryx-library-filters:${username}`;
}

function loadLibraryFilters(): LibraryFilterSelection {
  try {
    const stored = JSON.parse(localStorage.getItem(libraryFilterStorageKey()) ?? "null") as Partial<LibraryFilterSelection> | null;
    if (!stored || typeof stored !== "object") return libraryFilterDefaults;
    return {
      itemType: ["item", "part", "assembly"].includes(stored.itemType ?? "")
        ? stored.itemType as LibraryFilterSelection["itemType"]
        : "item",
      itemNumberPattern: typeof stored.itemNumberPattern === "string" ? stored.itemNumberPattern : "",
      namePattern: typeof stored.namePattern === "string" ? stored.namePattern : "",
      cadSystem: libraryCadSystems.includes(stored.cadSystem ?? "") ? stored.cadSystem! : "",
      createdBy: typeof stored.createdBy === "string" ? stored.createdBy : "",
      checkedOutBy: typeof stored.checkedOutBy === "string" ? stored.checkedOutBy : "",
    };
  } catch {
    return libraryFilterDefaults;
  }
}

function libraryFilterRequest(selection: LibraryFilterSelection) {
  return {
    entity_type: selection.itemType,
    filters: {
      ...(selection.cadSystem ? { cad_system: selection.cadSystem } : {}),
      ...(selection.itemNumberPattern ? { item_number_regex: selection.itemNumberPattern } : {}),
      ...(selection.namePattern ? { name_regex: selection.namePattern } : {}),
      ...(selection.createdBy ? { owner: selection.createdBy } : {}),
      ...(selection.checkedOutBy ? { checkout_owner: selection.checkedOutBy } : {}),
    },
    limit: 50,
  };
}

function cadSystemLabel(value?: string | null): string {
  const normalized = value?.trim().toLowerCase() ?? "";
  if (!normalized) return "—";
  if (normalized === "step") return "STEP";
  if (normalized.includes("freecad")) return "FreeCAD";
  if (normalized.includes("autocad")) return "AutoCAD";
  if (normalized.includes("inventor")) return "Inventor";
  if (normalized.includes("solidworks")) return "SOLIDWORKS";
  if (normalized.includes("fusion")) return "Fusion";
  if (normalized.includes("creo")) return "Creo";
  if (normalized.includes("catia")) return "CATIA";
  if (normalized.includes("nx")) return "NX";
  return value?.trim() || "—";
}

function numericValue(value: unknown, unit = "", digits = 2): string {
  if (typeof value !== "number") return "—";
  const formatted = new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
  return unit ? `${formatted} ${unit}` : formatted;
}

function predicateLabel(value: NumericPredicate, unit: string): string {
  const labels: Record<string, string> = {
    eq: "=", lt: "<", lte: "≤", gt: ">", gte: "≥", target: "≈",
  };
  return Object.entries(labels)
    .filter(([key]) => value[key as keyof NumericPredicate] != null)
    .map(([key, operator]) => `${operator} ${value[key as keyof NumericPredicate]} ${unit}`)
    .join(" ");
}

function interpretationChips(request?: EngineeringSearchRequest): Chip[] {
  if (!request) return [];
  const chips: Chip[] = [{ key: "entity_type", label: request.entity_type }];
  request.text.terms.forEach((term, index) =>
    chips.push({ key: `term:${index}`, label: term }),
  );
  request.filters.identifiers.forEach((value, index) =>
    chips.push({ key: `identifier:${index}`, label: value }),
  );
  const filters = request.filters;
  if (filters.material) {
    chips.push({
      key: "material",
      label: [filters.material.family, filters.material.grade, filters.material.temper]
        .filter(Boolean).join(" "),
    });
  }
  if (filters.material_presence) {
    chips.push({ key: "material_presence", label: `material: ${filters.material_presence}` });
  }
  filters.feature_filters.forEach((value, index) => {
    chips.push({
      key: `feature:${index}`,
      label: `${value.feature}: ${value.presence}`,
    });
  });
  Object.entries(filters.geometry ?? {}).forEach(([field, predicate]) => {
    if (!predicate) return;
    const unit = field.includes("volume") ? "mm³" : field.includes("area") ? "mm²" : "mm";
    chips.push({
      key: `geometry:${field}`,
      label: `${field.replaceAll("_", " ")} ${predicateLabel(predicate, unit)}`,
    });
  });
  if (filters.mass_kg) {
    chips.push({ key: "mass_kg", label: `mass ${predicateLabel(filters.mass_kg, "kg")}` });
  }
  (["item_number_regex", "name_regex", "classification", "cad_system", "artifact_format", "revision", "status", "lifecycle",
    "owner", "approval_state", "checkout_owner", "artifact_presence", "extraction_status"] as const).forEach(
    (field) => {
      if (filters[field]) {
        chips.push({ key: field, label: `${field.replace("_", " ")}: ${filters[field]}` });
      }
    },
  );
  if (filters.approval_assigned_to_me) chips.push({ key: "approval_assigned_to_me", label: "assigned to me" });
  if (filters.approval_due_before) chips.push({ key: "approval_due_before", label: "approval overdue" });
  if (filters.artifact_missing) chips.push({ key: "artifact_missing", label: "artifact: missing" });
  if (filters.has_warnings) chips.push({ key: "has_warnings", label: "has warnings" });
  request.relationships.forEach((value, index) => chips.push({
    key: `relationship:${index}`,
    label: `${value.type.replaceAll("_", " ")}: ${value.depth}`,
  }));
  if (request.similarity) chips.push({ key: "similarity", label: "metadata similarity" });
  return chips;
}

function withoutChip(request: EngineeringSearchRequest, key: string): EngineeringSearchRequest {
  const next = structuredClone(request);
  if (key === "entity_type") next.entity_type = "item";
  else if (key.startsWith("term:")) next.text.terms.splice(Number(key.split(":")[1]), 1);
  else if (key.startsWith("identifier:")) {
    next.filters.identifiers.splice(Number(key.split(":")[1]), 1);
  } else if (key.startsWith("geometry:")) {
    if (next.filters.geometry) delete next.filters.geometry[key.split(":")[1]];
  } else if (key.startsWith("feature:")) {
    next.filters.feature_filters.splice(Number(key.split(":")[1]), 1);
  } else if (key.startsWith("relationship:")) {
    next.relationships.splice(Number(key.split(":")[1]), 1);
  } else if (key === "similarity") {
    next.similarity = null;
  } else if (key === "material" || key === "mass_kg") next.filters[key] = null;
  else if (key === "material_presence") next.filters.material_presence = null;
  else if (["item_number_regex", "name_regex", "classification", "cad_system", "artifact_format", "revision", "status", "lifecycle",
    "owner", "approval_state", "approval_assigned_to_me", "approval_due_before", "checkout_owner",
    "artifact_presence", "artifact_missing", "extraction_status", "has_warnings"].includes(key)) {
    (next.filters as Record<string, unknown>)[key] = null;
  }
  return next;
}

export function AdvancedSearch() {
  const [initialFilters] = useState(loadLibraryFilters);
  const [query, setQuery] = useState("");
  const [itemType, setItemType] = useState<"item" | "part" | "assembly">(initialFilters.itemType);
  const [itemNumberPattern, setItemNumberPattern] = useState(initialFilters.itemNumberPattern);
  const [namePattern, setNamePattern] = useState(initialFilters.namePattern);
  const [cadSystem, setCadSystem] = useState(initialFilters.cadSystem);
  const [createdBy, setCreatedBy] = useState(initialFilters.createdBy);
  const [checkedOutBy, setCheckedOutBy] = useState(initialFilters.checkedOutBy);
  const [users, setUsers] = useState<LibraryUser[]>([]);
  const [response, setResponse] = useState<EngineeringSearchResponse>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedbackState, setFeedbackState] = useState<"idle" | "sending" | "sent">("idle");
  const [pageInput, setPageInput] = useState("1");
  const [rowMenu, setRowMenu] = useState<{
    result: EngineeringSearchResult;
    x: number;
    y: number;
  }>();
  const navigate = useNavigate();
  const currentUsername = localStorage.getItem("modelryx-username")?.trim().toLocaleLowerCase();
  const selectableUsers = users.filter(
    (user) => user.username.toLocaleLowerCase() !== currentUsername,
  );

  const matchingRevisionTarget = (result: EngineeringSearchResult) => result.revision_id
    ? `/items/${result.item_id}/revisions/${result.revision_id}`
    : `/items/${result.item_id}`;

  useEffect(() => {
    if (!rowMenu) return;
    const close = () => setRowMenu(undefined);
    window.addEventListener("click", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("blur", close);
    };
  }, [rowMenu]);

  const execute = (path: "/ai/search" | "/search", body: unknown) => {
    setLoading(true);
    setError("");
    setFeedbackState("idle");
    api<EngineeringSearchResponse>(path, "POST", body)
      .then(setResponse)
      .catch((reason: Error) => {
        setResponse(undefined);
        setError(reason.message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    api<LibraryUser[]>("/me/library-users")
      .then((availableUsers) => {
        setUsers(availableUsers);
        const restoreUserFilter = (value: string) => {
          if (!value || value === "$me") return value;
          if (availableUsers.some((user) => user.username === value)) return value;
          const displayMatches = availableUsers.filter((user) => user.display_name === value);
          return displayMatches.length === 1 ? displayMatches[0].username : "";
        };
        const restored = {
          ...initialFilters,
          createdBy: restoreUserFilter(initialFilters.createdBy),
          checkedOutBy: restoreUserFilter(initialFilters.checkedOutBy),
        };
        setCreatedBy(restored.createdBy);
        setCheckedOutBy(restored.checkedOutBy);
        execute("/search", libraryFilterRequest(restored));
      })
      .catch(() => {
        setUsers([]);
        execute("/search", libraryFilterRequest(initialFilters));
      });
  }, []);

  useEffect(() => {
    localStorage.setItem(libraryFilterStorageKey(), JSON.stringify({
      itemType,
      itemNumberPattern,
      namePattern,
      cadSystem,
      createdBy,
      checkedOutBy,
    } satisfies LibraryFilterSelection));
  }, [cadSystem, checkedOutBy, createdBy, itemNumberPattern, itemType, namePattern]);
  const chips = useMemo(() => interpretationChips(response?.interpretation), [response]);

  const applyLibraryFilters = () => execute("/search", libraryFilterRequest({
    itemType, itemNumberPattern, namePattern, cadSystem, createdBy, checkedOutBy,
  }));

  const changePage = (offset: number) => {
    if (!response) return;
    execute("/search", {
      ...response.interpretation,
      offset: Math.max(0, offset),
    });
  };
  const pageSize = response?.interpretation.limit ?? 50;
  const totalPages = Math.max(1, Math.ceil((response?.total ?? 0) / pageSize));
  const currentPage = response
    ? Math.min(totalPages, Math.floor(response.interpretation.offset / pageSize) + 1)
    : 1;
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
    if (page !== currentPage) changePage((page - 1) * pageSize);
  };

  const columns: ColumnDef<EngineeringSearchResult, unknown>[] = [
    {
      id: "item_number", header: "Item Number", accessorFn: (row) => row.item_number,
      cell: ({ getValue }) => <strong>{getValue() as string}</strong>,
      enableHiding: false,
    },
    { id: "name", header: "Name", accessorFn: (row) => row.name },
    {
      id: "item_type", header: "Part Type", accessorFn: (row) => row.item_type,
      cell: ({ getValue }) => {
        const value = String(getValue() ?? "");
        return value ? value.charAt(0) + value.slice(1).toLowerCase() : "—";
      },
    },
    {
      id: "cad_system", header: "CAD System", accessorFn: (row) => row.cad_system ?? "—",
      cell: ({ row }) => cadSystemLabel(row.original.cad_system),
    },
    {
      id: "revision", header: "Revision", accessorFn: (row) => row.revision_code,
      cell: ({ getValue }) => getValue() ? `Rev ${getValue()}` : "—",
    },
    {
      id: "match", header: "Match", accessorFn: (row) => row.score,
      cell: ({ row }) => (
        <span title={row.original.match_reasons
          .map((reason) => `${reason.type}: ${reason.query}`).join("\n")}>
          {Math.round(row.original.score * 100)}%
        </span>
      ),
    },
    { id: "description", header: "Description", accessorFn: (row) => row.description ?? "—" },
    { id: "category", header: "Category", accessorFn: (row) => row.category ?? "—" },
    { id: "item_status", header: "Item Status", accessorFn: (row) => row.item_status ?? "—" },
    { id: "metadata_status", header: "Metadata Status", accessorFn: (row) => row.metadata_status ?? "—" },
    { id: "material", header: "Material", accessorFn: (row) => row.material ?? "—" },
    {
      id: "dimensions_mm", header: "Overall Size", accessorFn: (row) => row.dimensions_mm,
      cell: ({ getValue }) => {
        const value = getValue() as number[] | null | undefined;
        return value?.length === 3 ? value.map((entry) => numericValue(entry, "mm")).join(" × ") : "—";
      },
    },
    { id: "mass_kg", header: "Mass", accessorFn: (row) => row.mass_kg, cell: ({ getValue }) => numericValue(getValue(), "kg", 4) },
    { id: "volume_mm3", header: "Volume", accessorFn: (row) => row.volume_mm3, cell: ({ getValue }) => numericValue(getValue(), "mm³") },
    { id: "surface_area_mm2", header: "Surface Area", accessorFn: (row) => row.surface_area_mm2, cell: ({ getValue }) => numericValue(getValue(), "mm²") },
    { id: "solid_count", header: "Solids", accessorFn: (row) => row.solid_count ?? "—" },
    { id: "face_count", header: "Faces", accessorFn: (row) => row.face_count ?? "—" },
    { id: "edge_count", header: "Edges", accessorFn: (row) => row.edge_count ?? "—" },
    { id: "hole_count", header: "Holes", accessorFn: (row) => row.hole_count ?? "—" },
    { id: "fillet_count", header: "Fillets", accessorFn: (row) => row.fillet_count ?? "—" },
    { id: "chamfer_count", header: "Chamfers", accessorFn: (row) => row.chamfer_count ?? "—" },
    { id: "extrusion_count", header: "Extrusions", accessorFn: (row) => row.extrusion_count ?? "—" },
    { id: "revolution_count", header: "Revolutions", accessorFn: (row) => row.revolution_count ?? "—" },
    { id: "pocket_count", header: "Pockets", accessorFn: (row) => row.pocket_count ?? "—" },
    { id: "pattern_count", header: "Patterns", accessorFn: (row) => row.pattern_count ?? "—" },
    { id: "thread_count", header: "Threads", accessorFn: (row) => row.thread_count ?? "—" },
  ];

  const defaultColumnVisibility = {
    description: false, category: false, item_status: false, metadata_status: false,
    material: false, dimensions_mm: false, mass_kg: false, volume_mm3: false,
    surface_area_mm2: false, solid_count: false, face_count: false, edge_count: false,
    hole_count: false, fillet_count: false, chamfer_count: false, extrusion_count: false,
    revolution_count: false, pocket_count: false, pattern_count: false, thread_count: false,
  };

  return (
    <section className="page engineering-search-page">
      <div className="engineering-search-heading">
        <div><p className="eyebrow">AI ENGINEERING SEARCH</p><h1>Engineering search</h1></div>
        <Link className="button" to="/checkin">New Item</Link>
      </div>
      <form className="engineering-search-box" onSubmit={(event) => {
        event.preventDefault();
        const submittedQuery = query.trim();
        // The Library prompt represents a complete new search, not a conversational
        // refinement. Clear the previous interpretation immediately and omit its
        // session ID so stale terms and filters cannot carry into the new prompt.
        setResponse(undefined);
        if (submittedQuery) execute("/ai/search", {
          query: submittedQuery,
          limit: 50,
          session_id: null,
        });
        else execute("/search", { limit: 50 });
      }}>
        <input value={query} onChange={(event) => setQuery(event.target.value)}
          placeholder="Find aluminum motor housings between 100 and 150 mm wide…"
          aria-label="Ask Modelryx" />
        <button disabled={loading}>{loading ? "Searching…" : "Ask Modelryx"}</button>
      </form>
      <div className="library-quick-filters" aria-label="Library filters">
        <label>
          <span>Item Number</span>
          <input value={itemNumberPattern} onChange={(event) => setItemNumberPattern(event.target.value)}
            placeholder="^PART-00.*" title="Case-insensitive regular expression" />
        </label>
        <label>
          <span>Name</span>
          <input value={namePattern} onChange={(event) => setNamePattern(event.target.value)}
            placeholder="mount(ing)? plate" title="Case-insensitive regular expression" />
        </label>
        <label>
          <span>Part Type</span>
          <select value={itemType} onChange={(event) => setItemType(event.target.value as typeof itemType)}>
            <option value="item">All types</option>
            <option value="part">Part</option>
            <option value="assembly">Assembly</option>
          </select>
        </label>
        <label>
          <span>Created by</span>
          <select value={createdBy} onChange={(event) => setCreatedBy(event.target.value)}>
            <option value="">Any user</option>
            <option value="$me">Me</option>
            {selectableUsers.map((user) => (
              <option key={user.id} value={user.username}>
                {user.display_name} ({user.username})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Checked out by</span>
          <select value={checkedOutBy} onChange={(event) => setCheckedOutBy(event.target.value)}>
            <option value="">Anyone or not checked out</option>
            <option value="$me">Me</option>
            {selectableUsers.map((user) => (
              <option key={user.id} value={user.username}>
                {user.display_name} ({user.username})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>CAD System</span>
          <select value={cadSystem} onChange={(event) => setCadSystem(event.target.value)}>
            <option value="">All systems</option>
            {libraryCadSystems.map((system) => <option key={system} value={system}>{system}</option>)}
          </select>
        </label>
        <button type="button" className="button secondary" disabled={loading}
          onClick={applyLibraryFilters}>Apply filters</button>
        {(itemType !== "item" || itemNumberPattern || namePattern || cadSystem || createdBy || checkedOutBy) && (
          <button type="button" className="button secondary" disabled={loading}
            onClick={() => {
              setItemType("item");
              setItemNumberPattern("");
              setNamePattern("");
              setCadSystem("");
              setCreatedBy("");
              setCheckedOutBy("");
              execute("/search", { limit: 50 });
            }}>Clear</button>
        )}
      </div>
      {chips.length > 0 && <div className="interpretation-panel">
        <span>Interpreted as</span>
        <div className="filter-chips">{chips.map((chip) => (
          <button key={chip.key} className="filter-chip" type="button" title="Remove filter"
            onClick={() => {
              if (response) execute("/search", withoutChip(response.interpretation, chip.key));
            }}>
            {chip.label}<span aria-hidden="true">×</span>
          </button>
        ))}</div>
      </div>}
      {response?.warnings.map((warning) =>
        <p className="search-warning" key={warning}>{warning}</p>,
      )}
      {response && response.coverage.excluded_unknown > 0 && (
        <p className="search-warning">
          {response.coverage.excluded_unknown} authorized record(s) were excluded because required
          metadata coverage is unknown ({response.coverage.coverage_rules.join(", ")}).
        </p>
      )}
      {response?.session_id && <div className="search-feedback">
        <span>Was this interpretation incorrect?</span>
        <button type="button" className="button secondary"
          disabled={feedbackState !== "idle"}
          onClick={() => {
            setFeedbackState("sending");
            api(`/ai/search/sessions/${response.session_id}/feedback`, "POST", {
              category: "incorrect_interpretation",
            }).then(() => setFeedbackState("sent")).catch((reason: Error) => {
              setFeedbackState("idle");
              setError(reason.message);
            });
          }}>
          {feedbackState === "sending" ? "Sending…"
            : feedbackState === "sent" ? "Feedback recorded" : "Report interpretation"}
        </button>
      </div>}
      {error && <p className="error">{error}</p>}
      <div className="search-summary">
        <span>{response && response.total > 0
          ? `${response.interpretation.offset + 1}–${Math.min(
            response.interpretation.offset + response.results.length,
            response.total,
          )} of ${response.total} results`
          : "0 results"}</span>
        {response && <span>{response.trace.parser} interpretation · {response.trace.duration_ms.toFixed(0)} ms
          {response.session_id ? " · conversation active" : ""}</span>}
      </div>
      <DataTable columns={columns} data={response?.results ?? []} getRowId={(row) => `${row.item_id}:${row.revision_id ?? ""}`} initialPageSize={50} hidePagination defaultColumnVisibility={defaultColumnVisibility}
        loading={loading} emptyMessage="No items match the interpreted search"
        rowActions={(result) => [
          { label: "Open item", onSelect: () => navigate(`/items/${result.item_id}`) },
          ...(result.revision_id ? [{ label: "Open matching revision", onSelect: () => navigate(matchingRevisionTarget(result)) }, { label: "Compare revisions", onSelect: () => navigate(`/items/${encodeURIComponent(result.item_id)}/revisions/${encodeURIComponent(result.revision_id!)}?section=compare&to=${encodeURIComponent(result.revision_id!)}`) }] : []),
        ]} />
      {response && response.total > response.interpretation.limit && (
        <nav className="library-pagination" aria-label="Library pages">
          <button type="button" className="button secondary" disabled={loading || currentPage === 1}
            onClick={() => changePage(0)}>
            First
          </button>
          <button type="button" className="button secondary" disabled={loading || currentPage === 1}
            onClick={() => changePage((currentPage - 2) * pageSize)}>
            Previous
          </button>
          <form onSubmit={browseToPage}>
            <label htmlFor="library-page-number">Page</label>
            <input
              id="library-page-number"
              type="number"
              min="1"
              max={totalPages}
              value={pageInput}
              disabled={loading}
              onChange={(event) => setPageInput(event.target.value)}
              aria-label={`Page number, 1 through ${totalPages}`}
            />
            <span>of {totalPages}</span>
            <button type="submit" className="button secondary" disabled={loading}>Go</button>
          </form>
          <span className="library-page-total">{response.total} results</span>
          <button type="button" className="button secondary"
            disabled={loading || currentPage === totalPages}
            onClick={() => changePage(currentPage * pageSize)}>
            Next
          </button>
          <button type="button" className="button secondary"
            disabled={loading || currentPage === totalPages}
            onClick={() => changePage((totalPages - 1) * pageSize)}>
            Last
          </button>
        </nav>
      )}
      {!loading && response?.total === 0 && chips.length > 0 && (
        <div className="search-empty-recovery">
          <p>No records matched all interpreted constraints.</p>
          <button type="button" className="button secondary"
            onClick={() => execute("/search", { limit: 50 })}>
            Clear interpreted filters
          </button>
        </div>
      )}
      {rowMenu && (
        <nav
          className="search-row-menu"
          style={{ left: rowMenu.x, top: rowMenu.y }}
          aria-label={`Actions for ${rowMenu.result.item_number}`}
          onClick={(event) => event.stopPropagation()}
        >
          <Link to={`/items/${rowMenu.result.item_id}`}>Open item</Link>
          {rowMenu.result.revision_id && (
            <Link to={matchingRevisionTarget(rowMenu.result)}>Open matching revision</Link>
          )}
          {rowMenu.result.revision_id && (
            <Link to={`/items/${encodeURIComponent(rowMenu.result.item_id)}/revisions/${encodeURIComponent(rowMenu.result.revision_id!)}?section=compare&to=${encodeURIComponent(rowMenu.result.revision_id!)}`}>
              Compare revisions…
            </Link>
          )}
        </nav>
      )}
    </section>
  );
}
