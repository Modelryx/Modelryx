import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { fetchPreviewBlob, type AssistantViewerCommand } from "../api";
import { Button } from "../components/Button";
import { Link } from "react-router-dom";
import type { ModelryxBom, ModelryxBomItem, ModelryxViewer, StandardView } from "../viewer";

type ViewerMenu = { kind: "canvas" | "tree"; x: number; y: number; ids: string[]; trigger: HTMLElement | null };
export type PreviewEngineeringDetails = { itemNumber: string; itemName: string; revisionCode: string; revisionPath: string; referenceDesignator: string | null; occurrenceKey: string; sourceReference: string; quantity: number; isSuppressed: boolean; isVisible: boolean };
export function ModelPreview({ revisionId, revisionCode, filename, modelUrl, snapshotId, onBomChange, onSelectionChange, viewerCommand, engineeringDetails }: { revisionId: string; revisionCode: string; filename: string; modelUrl: string; snapshotId?: string; onBomChange?: (bom: ModelryxBom | undefined) => void; onSelectionChange?: (occurrenceIds: string[]) => void; viewerCommand?: AssistantViewerCommand; engineeringDetails?: Map<string, PreviewEngineeringDetails> }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<ModelryxViewer | null>(null);
  const appliedCommandRef = useRef("");
  const menuRef = useRef<HTMLDivElement>(null);
  const submenuRef = useRef<HTMLDivElement>(null);
  const propertiesDialogRef = useRef<HTMLDialogElement>(null);
  const propertiesReturnFocusRef = useRef<HTMLElement | null>(null);
  const selectedRef = useRef<string[]>([]);
  const callbacks = useRef({ onBomChange, onSelectionChange });
  callbacks.current = { onBomChange, onSelectionChange };
  const [bom, setBom] = useState<ModelryxBom>();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [selected, setSelected] = useState<string[]>([]);
  const [visibilityVersion, setVisibilityVersion] = useState(0);
  const [menu, setMenu] = useState<ViewerMenu | null>(null);
  const [submenu, setSubmenu] = useState<"selection" | "model" | "view" | "display" | "measure" | null>(null);
  const [transparency, setTransparency] = useState(0);
  const [measurement, setMeasurement] = useState("");
  const [propertiesItem, setPropertiesItem] = useState<ModelryxBomItem>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const refreshVisibility = () => setVisibilityVersion(value => value + 1);
  useEffect(() => {
    const controller = new AbortController();
    let viewer: ModelryxViewer | undefined;
    const container = canvasRef.current;
    setBom(undefined); setSelected([]); selectedRef.current = []; setError(""); setLoading(true); setTransparency(0); setPropertiesItem(undefined);
    appliedCommandRef.current = "";
    callbacks.current.onBomChange?.(undefined);
    callbacks.current.onSelectionChange?.([]);
    const context = (event: Event) => {
      const position = (event as CustomEvent<{x: number; y: number}>).detail;
      if (viewerRef.current && position) setMenu({ kind: "canvas", ...position, ids: [...selectedRef.current], trigger: container });
    };
    container?.addEventListener("modelryx-contextmenu", context);
    const run = async () => {
      try {
        const { ModelryxViewer } = await import("../viewer");
        if (controller.signal.aborted || !container) return;
        viewer = new ModelryxViewer(container, { renderMode: "shaded" });
        viewerRef.current = viewer;
        viewer.on("measurementChanged", event => {
          if (event.state === "started") setMeasurement("Select points on the model to measure.");
          else if (event.state === "completed") setMeasurement(event.formattedValue || event.formattedDistance || "Measurement complete.");
          else if (event.state === "cancelled" || event.state === "cleared") setMeasurement("");
        });
        viewer.on("bomLoaded", () => {
          const tree = viewer?.getBom();
          setBom(tree);
          callbacks.current.onBomChange?.(tree);
          if (tree) setExpanded(new Set([tree.rootOccurrenceId, ...tree.items.filter(item => item.parentOccurrenceId === tree.rootOccurrenceId).map(item => item.occurrenceId)]));
        });
        viewer.on("bomCleared", () => { setBom(undefined); setExpanded(new Set()); callbacks.current.onBomChange?.(undefined); });
        viewer.on("selectionChanged", event => { selectedRef.current = event.occurrenceIds; setSelected(event.occurrenceIds); callbacks.current.onSelectionChange?.(event.occurrenceIds); });
        let lastError: unknown;
        let loaded = false;
        for (const candidate of snapshotId ? [modelUrl] : [`/revisions/${encodeURIComponent(revisionId)}/preview`, modelUrl]) {
          try {
            const blob = await fetchPreviewBlob(candidate, controller.signal);
            await viewer.load(new File([blob], candidate.endsWith("/preview") ? `revision-${revisionCode}.pfg` : filename, { type: blob.type }), { signal: controller.signal });
            loaded = true; lastError = undefined; break;
          } catch (reason) { if (controller.signal.aborted) return; lastError = reason; }
        }
        if (!loaded) throw lastError ?? new Error("Unable to load the model.");
        // A standalone GLB cannot read its matching sidecar from a browser File.
        if (!viewer.getBom()) {
          try { const metadata = await fetchPreviewBlob(`/revisions/${encodeURIComponent(revisionId)}/preview/metadata${snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ""}`, controller.signal); await viewer.loadBom(metadata); }
          catch { /* Navigation data is optional; the model remains available. */ }
        }
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load preview"); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    };
    void run();
    return () => { controller.abort(); container?.removeEventListener("modelryx-contextmenu", context); viewer?.dispose(); if (viewerRef.current === viewer) viewerRef.current = null; };
  }, [revisionId, revisionCode, filename, modelUrl, snapshotId, attempt]);
  useEffect(() => {
    if (!viewerCommand || loading || error || !viewerRef.current || !viewerCommand.occurrence_ids.length
      || appliedCommandRef.current === viewerCommand.id) return;
    if (viewerCommand.type === "ISOLATE_OCCURRENCES") viewerRef.current.isolateOccurrences(viewerCommand.occurrence_ids);
    viewerRef.current.selectOccurrences(viewerCommand.occurrence_ids);
    viewerRef.current.fitSelection();
    appliedCommandRef.current = viewerCommand.id;
  }, [viewerCommand, loading, error]);
  useEffect(() => {
    const dialog = propertiesDialogRef.current;
    if (propertiesItem && !dialog?.open) dialog?.showModal();
    else if (!propertiesItem && dialog?.open) dialog.close();
  }, [propertiesItem]);
  useEffect(() => {
    if (!menu) return;
    const panel = menuRef.current;
    setSubmenu(null);
    if (panel) { panel.style.left = `${Math.max(8, Math.min(menu.x, innerWidth - panel.offsetWidth - 8))}px`; panel.style.top = `${Math.max(8, Math.min(menu.y, innerHeight - panel.offsetHeight - 8))}px`; panel.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus(); }
    const outside = (event: PointerEvent) => { if (!panel?.contains(event.target as Node)) setMenu(null); };
    const key = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (target instanceof HTMLInputElement && event.key !== "Escape") return;
      const child = submenuRef.current;
      const inChild = !!child?.contains(target);
      if (event.key === "Escape") {
        event.preventDefault();
        if (inChild) { child?.previousElementSibling instanceof HTMLElement && child.previousElementSibling.focus(); setSubmenu(null); }
        else { setMenu(null); menu.trigger?.focus(); }
      } else if (event.key === "ArrowLeft" && inChild) {
        event.preventDefault(); child?.previousElementSibling instanceof HTMLElement && child.previousElementSibling.focus(); setSubmenu(null);
      } else if (event.key === "ArrowRight" && !inChild && target.dataset.submenu) {
        event.preventDefault(); target.click();
        window.requestAnimationFrame(() => submenuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus());
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const buttons = Array.from(inChild ? child?.querySelectorAll<HTMLButtonElement>(":scope > button:not(:disabled)") ?? [] : panel?.querySelectorAll<HTMLButtonElement>(":scope > button:not(:disabled), :scope > .preview-menu-group > button:not(:disabled)") ?? []);
        if (!buttons.length) return;
        event.preventDefault();
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
      }
    };
    const close = () => setMenu(null);
    const scroll = (event: Event) => { if (!panel?.contains(event.target as Node)) close(); };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", key); window.addEventListener("resize", close); window.addEventListener("scroll", scroll, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", key); window.removeEventListener("resize", close); window.removeEventListener("scroll", scroll, true); };
  }, [menu]);
  useEffect(() => {
    const child = submenuRef.current;
    const parent = child?.previousElementSibling;
    if (!child || !(parent instanceof HTMLElement)) return;
    const bounds = parent.getBoundingClientRect();
    const right = bounds.right + child.offsetWidth <= window.innerWidth - 8;
    const x = right ? bounds.right : bounds.left - child.offsetWidth;
    child.style.left = `${Math.max(8, Math.min(x, window.innerWidth - child.offsetWidth - 8))}px`;
    child.style.top = `${Math.max(8, Math.min(bounds.top, window.innerHeight - child.offsetHeight - 8))}px`;
  }, [submenu]);
  const byParent = new Map<string, ModelryxBomItem[]>();
  const items = bom?.items ?? [];
  const itemIds = new Set(items.map(item => item.occurrenceId));
  for (const item of items) {
    const parent = item.parentOccurrenceId && item.parentOccurrenceId !== item.occurrenceId && itemIds.has(item.parentOccurrenceId) ? item.parentOccurrenceId : "__root__";
    const siblings = byParent.get(parent);
    if (siblings) siblings.push(item); else byParent.set(parent, [item]);
  }
  const openForItem = (item: ModelryxBomItem, x: number, y: number, trigger: HTMLElement) => {
    const ids = selectedRef.current.includes(item.occurrenceId) ? selectedRef.current : [item.occurrenceId];
    if (!selectedRef.current.includes(item.occurrenceId)) viewerRef.current?.selectOccurrence(item.occurrenceId);
    setMenu({ kind: "tree", x, y, ids: [...ids], trigger });
  };
  const onItemKey = (event: KeyboardEvent<HTMLButtonElement>, item: ModelryxBomItem) => {
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); openForItem(item, rect.left, rect.bottom, event.currentTarget); }
  };
  const renderTree = (parent: string, ancestors: Set<string>): ReactNode => <ul className="preview-tree-list">{(byParent.get(parent) ?? []).filter(item => !ancestors.has(item.occurrenceId)).map(item => {
    const children = byParent.get(item.occurrenceId) ?? [];
    const isExpanded = expanded.has(item.occurrenceId);
    const visible = viewerRef.current?.isOccurrenceVisible(item.occurrenceId);
    const chosen = selected.includes(item.occurrenceId);
    const nextAncestors = new Set(ancestors); nextAncestors.add(item.occurrenceId);
    return <li key={item.occurrenceId}><div className="preview-tree-row">
      {children.length ? <button className="preview-tree-toggle" type="button" aria-label={`${isExpanded ? "Collapse" : "Expand"} ${item.name}`} aria-expanded={isExpanded} onClick={() => setExpanded(current => { const next = new Set(current); if (next.has(item.occurrenceId)) next.delete(item.occurrenceId); else next.add(item.occurrenceId); return next; })}>{isExpanded ? "▾" : "▸"}</button> : <span className="preview-tree-spacer" />}
      <button type="button" className="preview-visibility" disabled={visible === undefined} aria-label={`${visible === false ? "Show" : "Hide"} ${item.name}`} title={visible === undefined ? "No mapped model geometry" : visible ? "Hide in preview" : "Show in preview"} onClick={() => { if (visible === false) viewerRef.current?.showOccurrence(item.occurrenceId); else viewerRef.current?.hideOccurrence(item.occurrenceId); refreshVisibility(); }}>{visible === false ? "○" : "◉"}</button>
      <button type="button" className={`preview-tree-item${chosen ? " is-selected" : ""}${visible === false ? " is-hidden" : ""}`} aria-pressed={chosen} title={`${item.name}${item.revision ? ` · Revision ${item.revision}` : ""}`} onClick={event => { if (event.ctrlKey || event.metaKey) viewerRef.current?.toggleOccurrence(item.occurrenceId); else viewerRef.current?.selectOccurrence(item.occurrenceId); }} onContextMenu={event => { event.preventDefault(); openForItem(item, event.clientX, event.clientY, event.currentTarget); }} onKeyDown={event => onItemKey(event, item)}>{item.name}{item.revision ? ` · Rev ${item.revision}` : ""}</button>
      <button type="button" className="preview-tree-menu" aria-label={`Actions for ${item.name}`} aria-haspopup="menu" onClick={event => { const rect = event.currentTarget.getBoundingClientRect(); openForItem(item, rect.left, rect.bottom, event.currentTarget); }}>⋯</button>
    </div>{children.length > 0 && isExpanded && renderTree(item.occurrenceId, nextAncestors)}</li>;
  })}</ul>;
  const validSelection = !!menu?.ids.length && menu.ids.every(id => viewerRef.current?.isOccurrenceVisible(id) !== undefined);
  const perform = (action: () => void) => { action(); refreshVisibility(); const trigger = menu?.trigger; setMenu(null); trigger?.focus(); };
  const openCanvasMenu = (trigger: HTMLElement) => { if (!viewerRef.current || loading || error) return; const rect = trigger.getBoundingClientRect(); setMenu({ kind: "canvas", x: rect.left, y: rect.bottom, ids: [...selectedRef.current], trigger }); };
  const selectedEngineeringDetails = propertiesItem ? engineeringDetails?.get(propertiesItem.occurrenceId) : undefined;
  return <>
    {measurement && <p role="status" className="section-description">{measurement}</p>}
    {loading && <p role="status">Loading preview…</p>}{error && <div className="section-error" role="alert">{error}<Button variant="secondary" onClick={() => setAttempt(value => value + 1)}>Retry</Button></div>}
    <div className="model-preview-layout"><aside className="preview-navigation" aria-label="Preview model navigation"><div className="preview-navigation-header"><strong>Model</strong><span>Preview hierarchy</span></div>{loading ? <p>Loading model hierarchy…</p> : error ? <p>Model hierarchy is unavailable.</p> : bom?.items.length ? <div data-visibility-version={visibilityVersion} className="preview-tree-scroll">{renderTree("__root__", new Set())}</div> : <p>No model hierarchy is available for this preview.</p>}</aside><div className="model-preview" ref={canvasRef} tabIndex={0} aria-label="3D model preview" onKeyDown={event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); openCanvasMenu(event.currentTarget); } }} /></div>
    {menu && <div ref={menuRef} className="preview-context-menu" role="menu" aria-label={menu.kind === "canvas" ? "3D display actions" : "Model navigation actions"}>
      {menu.kind === "canvas" ? <>
        <div className="preview-menu-group">
          <button type="button" role="menuitem" data-submenu="view" aria-haspopup="menu" aria-expanded={submenu === "view"} onMouseEnter={() => setSubmenu("view")} onClick={() => setSubmenu("view")}>View<span aria-hidden="true">▸</span></button>
          {submenu === "view" && <div ref={submenuRef} className="preview-context-submenu" role="menu" aria-label="View actions">
            {([ ["iso", "Isometric"], ["front", "Front"], ["back", "Back"], ["left", "Left"], ["right", "Right"], ["top", "Top"], ["bottom", "Bottom"] ] as [StandardView, string][]).map(([view, name]) => <button type="button" role="menuitem" key={view} onClick={() => perform(() => viewerRef.current?.setView(view))}>{name}</button>)}
          </div>}
        </div>
        <span className="preview-menu-label">Display</span>
        <button type="button" role="menuitem" onMouseEnter={() => setSubmenu(null)} onClick={() => perform(() => viewerRef.current?.setRenderMode("shaded-edges"))}>Shaded + edges</button>
        <button type="button" role="menuitem" onMouseEnter={() => setSubmenu(null)} onClick={() => perform(() => viewerRef.current?.setRenderMode("shaded"))}>Shaded</button>
        <button type="button" role="menuitem" onMouseEnter={() => setSubmenu(null)} onClick={() => perform(() => viewerRef.current?.setRenderMode("wireframe"))}>Wireframe</button>
        <label className="preview-transparency" onMouseEnter={() => setSubmenu(null)}>Transparency <span><input type="range" min="0" max="100" value={transparency} onChange={event => { const value = Number(event.target.value); setTransparency(value); viewerRef.current?.setTransparency(value); }} /><output>{transparency}%</output></span></label>
        <div className="preview-menu-group">
          <button type="button" role="menuitem" data-submenu="measure" aria-haspopup="menu" aria-expanded={submenu === "measure"} onMouseEnter={() => setSubmenu("measure")} onClick={() => setSubmenu("measure")}>Measure<span aria-hidden="true">▸</span></button>
          {submenu === "measure" && <div ref={submenuRef} className="preview-context-submenu" role="menu" aria-label="Measurement actions">
            <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.startDistanceMeasurement())}>Distance</button>
            <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.startEdgeMeasurement())}>Edge length</button>
            <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.startAngleMeasurement())}>Angle</button>
            <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.startPointMeasurement())}>Point coordinates</button>
            <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.clearMeasurements())}>Clear measurements</button>
          </div>}
        </div>
        <span className="preview-menu-label preview-menu-divider">Model</span>
        <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.fitAll())}>Fit model</button>
        <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.fitVisible())}>Fit visible</button>
        <button type="button" role="menuitem" disabled={!validSelection} onClick={() => perform(() => viewerRef.current?.fitSelection())}>Fit selection</button>
        <button type="button" role="menuitem" onClick={() => perform(() => viewerRef.current?.showAll())}>Show all</button>
      </> : <>
        <button type="button" role="menuitem" disabled={menu.ids.length !== 1 || !viewerRef.current?.getBomItem(menu.ids[0])} onClick={() => { propertiesReturnFocusRef.current = menu.trigger; setPropertiesItem(viewerRef.current?.getBomItem(menu.ids[0])); setMenu(null); }}>Properties</button>
        <button type="button" role="menuitem" disabled={!validSelection} onClick={() => perform(() => viewerRef.current?.hideOccurrences(menu.ids))}>Hide</button>
        <button type="button" role="menuitem" disabled={!validSelection} onClick={() => perform(() => viewerRef.current?.showOccurrences(menu.ids))}>Show</button>
        <button type="button" role="menuitem" disabled={!validSelection} onClick={() => perform(() => viewerRef.current?.isolateOccurrences(menu.ids))}>Show only selections</button>
        <button type="button" role="menuitem" disabled={!validSelection} onClick={() => perform(() => viewerRef.current?.fitSelection())}>Fit selection</button>
      </>}
    </div>}
    <dialog ref={propertiesDialogRef} className="preview-properties-dialog" aria-labelledby="preview-properties-title"
      onClose={() => { setPropertiesItem(undefined); propertiesReturnFocusRef.current?.focus(); propertiesReturnFocusRef.current = null; }}
      onClick={event => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
      {propertiesItem && <>
        <div className="preview-properties-heading"><h3 id="preview-properties-title">{propertiesItem.name}</h3><button type="button" onClick={() => propertiesDialogRef.current?.close()} aria-label="Close properties">×</button></div>
        {selectedEngineeringDetails && <><h4>Engineering occurrence</h4><dl>
          <div><dt>Item</dt><dd><Link to={selectedEngineeringDetails.revisionPath}>{selectedEngineeringDetails.itemNumber}</Link> · {selectedEngineeringDetails.itemName}</dd></div>
          <div><dt>Referenced revision</dt><dd>{selectedEngineeringDetails.revisionCode}</dd></div>
          <div><dt>Reference designator</dt><dd>{selectedEngineeringDetails.referenceDesignator || "—"}</dd></div>
          <div><dt>Quantity</dt><dd>{selectedEngineeringDetails.quantity}</dd></div>
          <div><dt>Suppression</dt><dd>{selectedEngineeringDetails.isSuppressed ? "Suppressed" : "Not suppressed"}</dd></div>
          <div><dt>Recorded visibility</dt><dd>{selectedEngineeringDetails.isVisible ? "Visible" : "Hidden"}</dd></div>
          <div><dt>Occurrence key</dt><dd>{selectedEngineeringDetails.occurrenceKey}</dd></div>
          <div><dt>Source reference</dt><dd>{selectedEngineeringDetails.sourceReference || "—"}</dd></div>
        </dl></>}
        <h4>Preview node</h4><dl>
          <div><dt>Type</dt><dd>{propertiesItem.nodeType ?? "—"}</dd></div>
          <div><dt>Revision</dt><dd>{propertiesItem.revision ?? "—"}</dd></div>
          {propertiesItem.cadType && <div><dt>CAD type</dt><dd>{propertiesItem.cadType}</dd></div>}
          {propertiesItem.material && <div><dt>Material</dt><dd>{propertiesItem.material}</dd></div>}
          {propertiesItem.finish && <div><dt>Finish</dt><dd>{propertiesItem.finish}</dd></div>}
          {Object.entries(propertiesItem.properties ?? {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value ?? "—")}</dd></div>)}
        </dl>
      </>}
    </dialog>
  </>;
}
