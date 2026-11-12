import * as THREE from "three";
import { unzipSync } from "fflate";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { displayProfiles } from "./profiles";
import { createGltfLoader } from "./createGltfLoader";
import type {
  ComparisonMode, DisplayProfileName, LoadOptions, MeasurementType, MeasurementUnit, ModelryxBom, ModelryxBomItem, ModelryxMetadata, ModelryxViewerOptions, RenderMode,
  StandardView, ViewerEventName, ViewerEvents,
} from "./types";

type Listener<T> = (event: T) => void;

const SELECTABLE = "modelryxSelectable";
const EDGE_OBJECT = "modelryxEdges";

export class ModelryxViewer {
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 1, 0.01, 10_000);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly controls: OrbitControls;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly modelRoot = new THREE.Group();
  private comparisonFromRoot: THREE.Group | undefined;
  private comparisonToRoot: THREE.Group | undefined;
  private comparisonMode: ComparisonMode = "overlay";
  private readonly jointMarkers = new THREE.Group();
  private readonly partIndex = new Map<string, THREE.Object3D[]>();
  private readonly occurrenceIndex = new Map<string, THREE.Object3D[]>();
  private readonly listeners = new Map<ViewerEventName, Set<Listener<never>>>();
  private readonly resizeObserver: ResizeObserver;
  private readonly pmrem: THREE.PMREMGenerator;
  private readonly keyLight = new THREE.DirectionalLight(0xffffff);
  private readonly fillLight = new THREE.DirectionalLight(0xdbe8ff);
  private readonly rimLight = new THREE.DirectionalLight(0xd7e4ff);
  private readonly headLight = new THREE.DirectionalLight(0xffffff);
  private readonly selectionMaterial = new THREE.MeshBasicMaterial({ color: 0xffa000, transparent: true, opacity: 0.48, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
  private readonly selectionEdgeMaterial = new THREE.LineBasicMaterial({ color: 0xffd54f, depthTest: false, transparent: true, opacity: 0.95 });
  private readonly hoverMaterial = new THREE.MeshBasicMaterial({ color: 0x42a5f5, transparent: true, opacity: 0.24, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
  private readonly hoverEdgeMaterial = new THREE.LineBasicMaterial({ color: 0x90caf9, depthTest: false, transparent: true, opacity: 0.9 });
  private readonly selected = new Set<string>();
  private readonly selectedOccurrenceIds = new Set<string>();
  private selectedObjects: THREE.Object3D[] = [];
  private readonly sourceMaterialState = new WeakMap<THREE.Material, { opacity: number; transparent: boolean; depthWrite: boolean }>();
  private readonly resolvedAppearanceMeshes = new WeakSet<THREE.Mesh>();
  private readonly selectionOverlays = new THREE.Group();
  private readonly hoverOverlays = new THREE.Group();
  private readonly measurementRoot = new THREE.Group();
  private readonly measurementPointGeometry = new THREE.SphereGeometry(1, 16, 12);
  private readonly measurementPointMaterial = new THREE.MeshBasicMaterial({ color: 0xffc107, depthTest: false });
  private readonly measurementLineMaterial = new THREE.LineBasicMaterial({ color: 0xffc107, depthTest: false });
  private readonly measurementLabels: Array<{ position: THREE.Vector3; kind: MeasurementType; value: number | THREE.Vector3; element: HTMLSpanElement }> = [];
  private measurementDraft: THREE.Group | undefined;
  private readonly measurementPoints: THREE.Vector3[] = [];
  private measurementActive = false;
  private measurementType: MeasurementType = "distance";
  private measurementUnit: MeasurementUnit = "mm";
  private measurementMarkerSize = 0.01;
  private hoveredObject: THREE.Mesh | undefined;
  private hoveredOccurrenceId: string | undefined;
  private readonly gltfLoaders = createGltfLoader();
  private readonly loader = this.gltfLoaders.loader;
  private bom: ModelryxBom | undefined;
  private profile: DisplayProfileName;
  private renderMode: RenderMode;
  private edgeThresholdAngle: number;
  private transparency: number;
  private frameRequested = false;
  private loadRenderGeneration = 0;
  private readonly delayedRenderTimers = new Set<number>();
  private disposed = false;

  constructor(private readonly container: HTMLElement, options: ModelryxViewerOptions = {}) {
    this.profile = options.displayProfile ?? "cad";
    this.renderMode = options.renderMode ?? "shaded-edges";
    this.edgeThresholdAngle = options.edgeThresholdAngle ?? 20;
    this.transparency = this.normalizeTransparency(options.transparency ?? 0);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, options.maxPixelRatio ?? 2));
    this.renderer.domElement.className = "modelryx-viewer-canvas";
    // The host may keep loading, tooltip, and empty-state overlays in the
    // viewer container. Replace only an older renderer canvas, not host UI.
    this.container.querySelectorAll(":scope > .modelryx-viewer-canvas").forEach((canvas) => canvas.remove());
    this.container.prepend(this.renderer.domElement);

    this.camera.position.set(3, 2.4, 4.2);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.addEventListener("change", this.invalidate);
    this.scene.add(this.modelRoot, this.jointMarkers, this.measurementRoot, this.hoverOverlays, this.selectionOverlays);
    this.setupLighting();
    this.pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.setDisplayProfile(this.profile);
    this.setRenderMode(this.renderMode);
    this.renderer.domElement.addEventListener("pointerdown", this.handlePointerDown);
    this.renderer.domElement.addEventListener("pointermove", this.handlePointerMove);
    this.renderer.domElement.addEventListener("pointerleave", this.handlePointerLeave);
    this.renderer.domElement.addEventListener("contextmenu", this.handleCanvasContextMenu);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.handleContextRestored);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    this.resize();
  }

  async load(source: string | Blob | ArrayBuffer, options: LoadOptions = {}): Promise<void> {
    this.assertActive();
    this.clear();
    this.emit("loadingChanged", { loading: true });
    try {
      if (options.signal?.aborted) throw new DOMException("Model loading was aborted.", "AbortError");
      await this.waitForLayout();
      const pfg = await this.loadPfg(source, options);
      const gltf = pfg ? await this.parseGltf(pfg.glb) : await this.loadGltf(source, options);
      if (options.signal?.aborted) throw new DOMException("Model loading was aborted.", "AbortError");
      this.modelRoot.add(gltf.scene);
      gltf.scene.updateMatrixWorld(true);
      this.indexParts(gltf.scene);
      this.measurementMarkerSize = Math.max(new THREE.Box3().setFromObject(this.modelRoot).getSize(new THREE.Vector3()).length() * 0.006, 0.0005);
      this.fitAll();
      this.applyRenderMode();
      this.emit("loaded", { partCount: this.partIndex.size });
      if (pfg) this.setBom(this.parseBom(pfg.bom));
      else await this.loadMatchingBom(source);
      this.resize();
      this.renderNow();
      this.renderAfterLayoutSettles();
      this.warmUpAfterLoad();
      await this.commitInitialPresentation();
    } catch (error) {
      const viewerError = error instanceof Error ? error : new Error("Unable to load the GLB model.");
      this.emit("loadError", viewerError);
      throw viewerError;
    } finally {
      this.emit("loadingChanged", { loading: false });
    }
  }

  async loadComparison(
    fromSource: string | Blob | ArrayBuffer,
    toSource: string | Blob | ArrayBuffer,
    options: LoadOptions = {},
  ): Promise<void> {
    this.assertActive();
    this.clear();
    this.emit("loadingChanged", { loading: true });
    try {
      if (options.signal?.aborted) throw new DOMException("Model loading was aborted.", "AbortError");
      await this.waitForLayout();
      const [fromPfg, toPfg] = await Promise.all([
        this.loadPfg(fromSource, options),
        this.loadPfg(toSource, options),
      ]);
      const [fromGltf, toGltf] = await Promise.all([
        fromPfg ? this.parseGltf(fromPfg.glb) : this.loadGltf(fromSource, options),
        toPfg ? this.parseGltf(toPfg.glb) : this.loadGltf(toSource, options),
      ]);
      if (options.signal?.aborted) throw new DOMException("Model loading was aborted.", "AbortError");
      this.comparisonFromRoot = new THREE.Group();
      this.comparisonToRoot = new THREE.Group();
      this.comparisonFromRoot.name = "modelryx-comparison-from";
      this.comparisonToRoot.name = "modelryx-comparison-to";
      this.comparisonFromRoot.add(fromGltf.scene);
      this.comparisonToRoot.add(toGltf.scene);
      this.modelRoot.add(this.comparisonFromRoot, this.comparisonToRoot);
      this.styleComparisonRoot(this.comparisonFromRoot, 0xd65a5a, 0.48);
      this.styleComparisonRoot(this.comparisonToRoot, 0x36a269, 0.48);
      this.indexParts(this.comparisonFromRoot);
      this.indexParts(this.comparisonToRoot);
      this.setComparisonMode("overlay");
      this.measurementMarkerSize = Math.max(
        new THREE.Box3().setFromObject(this.modelRoot).getSize(new THREE.Vector3()).length() * 0.006,
        0.0005,
      );
      this.fitAll();
      this.applyRenderMode();
      this.emit("comparisonLoaded", {
        fromPartCount: this.countParts(this.comparisonFromRoot),
        toPartCount: this.countParts(this.comparisonToRoot),
        mode: this.comparisonMode,
      });
      this.resize();
      this.renderNow();
      this.renderAfterLayoutSettles();
      this.warmUpAfterLoad();
      await this.commitInitialPresentation();
    } catch (error) {
      const viewerError = error instanceof Error ? error : new Error("Unable to load comparison models.");
      this.emit("loadError", viewerError);
      throw viewerError;
    } finally {
      this.emit("loadingChanged", { loading: false });
    }
  }

  setComparisonMode(mode: ComparisonMode): void {
    this.assertActive();
    if (!this.comparisonFromRoot || !this.comparisonToRoot) {
      throw new Error("No revision comparison is loaded.");
    }
    this.comparisonMode = mode;
    this.comparisonFromRoot.visible = mode !== "to";
    this.comparisonToRoot.visible = mode !== "from";
    this.invalidate();
  }

  getComparisonMode(): ComparisonMode { return this.comparisonMode; }

  clear(): void {
    this.clearMeasurements();
    this.selected.clear();
    this.selectedOccurrenceIds.clear();
    this.selectedObjects = [];
    this.clearHover();
    this.partIndex.clear();
    this.occurrenceIndex.clear();
    this.comparisonFromRoot = undefined;
    this.comparisonToRoot = undefined;
    this.jointMarkers.clear();
    if (this.bom) {
      this.bom = undefined;
      this.emit("bomCleared", undefined);
    }
    this.selectionOverlays.clear();
    while (this.modelRoot.children.length) this.disposeObject(this.modelRoot.remove(this.modelRoot.children[0]));
    this.invalidate();
  }

  async loadBom(source: string | Blob | ArrayBuffer): Promise<ModelryxBom> {
    this.assertActive();
    const text = typeof source === "string"
      ? await (await fetch(source)).text()
      : new TextDecoder().decode(source instanceof Blob ? await source.arrayBuffer() : source);
    return this.setBom(this.parseBom(text));
  }

  getBom(): ModelryxBom | undefined { return this.bom; }
  getBomItem(occurrenceId: string): ModelryxBomItem | undefined { return this.bom?.items.find((item) => item.occurrenceId === occurrenceId); }
  getSelectedOccurrences(): ModelryxBomItem[] {
    return [...this.selectedOccurrenceIds].map((id) => this.getBomItem(id)).filter((item): item is ModelryxBomItem => Boolean(item));
  }
  selectOccurrence(occurrenceId: string): void {
    this.selectOccurrences([occurrenceId]);
  }
  toggleOccurrence(occurrenceId: string, source: "viewer" | "api" = "api"): void {
    const occurrenceIds = new Set(this.selectedOccurrenceIds);
    if (occurrenceIds.has(occurrenceId)) occurrenceIds.delete(occurrenceId);
    else occurrenceIds.add(occurrenceId);
    this.selectOccurrences([...occurrenceIds], source);
  }
  hideOccurrence(occurrenceId: string): void { this.setOccurrenceVisible(occurrenceId, false); }
  showOccurrence(occurrenceId: string): void { this.setOccurrenceVisible(occurrenceId, true); }
  hideOccurrences(occurrenceIds: string[]): void { occurrenceIds.forEach((id) => this.hideOccurrence(id)); }
  showOccurrences(occurrenceIds: string[]): void { occurrenceIds.forEach((id) => this.showOccurrence(id)); }
  isOccurrenceVisible(occurrenceId: string): boolean | undefined {
    const objects = this.objectsForOccurrence(occurrenceId);
    return objects.length ? objects.some((object) => object.visible) : undefined;
  }
  isolateOccurrence(occurrenceId: string): void {
    this.isolateOccurrences([occurrenceId]);
  }
  isolateOccurrences(occurrenceIds: string[]): void {
    const keep = new Set(occurrenceIds.flatMap((id) => this.objectsForOccurrence(id)));
    this.partIndex.forEach((objects) => objects.forEach((object) => { object.visible = keep.has(object); }));
    this.rebuildSelectionOverlays();
    this.invalidate();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.clear();
    this.resizeObserver.disconnect();
    this.controls.removeEventListener("change", this.invalidate);
    this.controls.dispose();
    this.gltfLoaders.dracoLoader.dispose();
    this.renderer.domElement.removeEventListener("pointerdown", this.handlePointerDown);
    this.renderer.domElement.removeEventListener("pointermove", this.handlePointerMove);
    this.renderer.domElement.removeEventListener("pointerleave", this.handlePointerLeave);
    this.renderer.domElement.removeEventListener("contextmenu", this.handleCanvasContextMenu);
    this.renderer.domElement.removeEventListener("webglcontextrestored", this.handleContextRestored);
    this.delayedRenderTimers.forEach((timer) => window.clearTimeout(timer));
    this.delayedRenderTimers.clear();
    this.selectionMaterial.dispose();
    this.selectionEdgeMaterial.dispose();
    this.hoverMaterial.dispose();
    this.hoverEdgeMaterial.dispose();
    this.measurementPointGeometry.dispose();
    this.measurementPointMaterial.dispose();
    this.measurementLineMaterial.dispose();
    this.scene.environment?.dispose();
    this.pmrem.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.listeners.clear();
  }

  fitAll(): void { this.fitObject(this.modelRoot); }
  /** Frames the currently visible model objects without changing the view direction. */
  fitVisible(): void {
    const visibleObjects = [...new Set([...this.partIndex.values()].flat())].filter((object) => this.isVisibleInScene(object));
    if (visibleObjects.length) this.fitObject(visibleObjects);
  }
  fitSelection(): void {
    const visibleSelection = this.selectedObjects.filter((object) => this.isVisibleInScene(object));
    if (visibleSelection.length) this.fitObject(visibleSelection);
  }

  startDistanceMeasurement(): void {
    this.startMeasurement("distance");
  }

  startAngleMeasurement(): void {
    this.startMeasurement("angle");
  }

  startEdgeMeasurement(): void {
    this.startMeasurement("edge");
  }

  startPointMeasurement(): void {
    this.startMeasurement("point");
  }

  private startMeasurement(type: MeasurementType): void {
    this.cancelMeasurement(false);
    this.measurementType = type;
    this.measurementActive = true;
    this.renderer.domElement.style.cursor = "crosshair";
    this.clearHover();
    this.renderer.domElement.style.cursor = "crosshair";
    this.emit("measurementChanged", { state: "started", pointCount: 0, unit: this.measurementUnit, measurementType: type });
  }

  cancelMeasurement(emitEvent = true): void {
    if (this.measurementDraft) {
      this.measurementRoot.remove(this.measurementDraft);
      this.measurementDraft = undefined;
    }
    this.measurementPoints.length = 0;
    const wasActive = this.measurementActive;
    this.measurementActive = false;
    this.renderer.domElement.style.cursor = "";
    if (emitEvent && wasActive) this.emit("measurementChanged", { state: "cancelled", unit: this.measurementUnit, measurementType: this.measurementType });
    this.invalidate();
  }

  clearMeasurements(): void {
    this.cancelMeasurement(false);
    this.measurementRoot.traverse((object) => { if (object instanceof THREE.Line) object.geometry.dispose(); });
    this.measurementRoot.clear();
    this.measurementLabels.forEach(({ element }) => element.remove());
    this.measurementLabels.length = 0;
    this.emit("measurementChanged", { state: "cleared", unit: this.measurementUnit });
    this.invalidate();
  }

  setMeasurementUnit(unit: MeasurementUnit): void {
    this.measurementUnit = unit;
    this.measurementLabels.forEach((measurement) => { measurement.element.textContent = this.formatMeasurement(measurement.kind, measurement.value); });
    this.invalidate();
  }

  setView(view: StandardView): void {
    const distance = this.camera.position.distanceTo(this.controls.target) || 5;
    const positions: Record<StandardView, THREE.Vector3> = {
      front: new THREE.Vector3(0, 0, 1), back: new THREE.Vector3(0, 0, -1),
      left: new THREE.Vector3(-1, 0, 0), right: new THREE.Vector3(1, 0, 0),
      top: new THREE.Vector3(0, 1, 0), bottom: new THREE.Vector3(0, -1, 0), iso: new THREE.Vector3(1, 0.8, 1),
    };
    this.camera.position.copy(positions[view].normalize().multiplyScalar(distance).add(this.controls.target));
    this.camera.lookAt(this.controls.target);
    this.controls.update();
    this.invalidate();
  }

  select(partIds: string[]): void { this.setSelection(partIds, "api"); }
  hide(partIds: string[]): void { this.setVisible(partIds, false); }
  show(partIds: string[]): void { this.setVisible(partIds, true); }
  showAll(): void { this.partIndex.forEach((objects) => objects.forEach((object) => { object.visible = true; })); this.rebuildSelectionOverlays(); this.invalidate(); }
  isolate(partIds: string[]): void {
    const keep = new Set(partIds);
    this.partIndex.forEach((objects, id) => objects.forEach((object) => { object.visible = keep.has(id); }));
    this.rebuildSelectionOverlays();
    this.invalidate();
  }

  setDisplayProfile(profile: DisplayProfileName): void {
    this.profile = profile;
    const value = displayProfiles[profile];
    this.scene.background = this.createGradientBackground(value.backgroundTop, value.backgroundBottom);
    this.scene.environmentIntensity = value.environmentIntensity;
    this.keyLight.intensity = value.keyIntensity;
    this.fillLight.intensity = value.fillIntensity;
    this.rimLight.intensity = value.rimIntensity;
    this.headLight.intensity = value.headlightIntensity;
    this.invalidate();
  }

  setRenderMode(mode: RenderMode): void { this.renderMode = mode; this.applyRenderMode(); this.invalidate(); }
  setTransparency(percentage: number): void { this.transparency = this.normalizeTransparency(percentage); this.applyRenderMode(); this.invalidate(); }
  setSelectionColor(color: string): void {
    if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error("Selection color must be a six-digit hex color.");
    this.selectionMaterial.color.set(color);
    this.selectionEdgeMaterial.color.set(color);
    this.rebuildSelectionOverlays();
    this.invalidate();
  }

  on<K extends ViewerEventName>(event: K, listener: Listener<ViewerEvents[K]>): () => void {
    const collection = this.listeners.get(event) ?? new Set<Listener<never>>();
    collection.add(listener as Listener<never>);
    this.listeners.set(event, collection);
    return () => collection.delete(listener as Listener<never>);
  }

  private setupLighting(): void {
    this.keyLight.position.set(-4, 5, 6);
    this.fillLight.position.set(5, 2, 3);
    this.rimLight.position.set(0, 5, -5);
    this.headLight.position.set(0, 0, 1);
    this.camera.add(this.headLight);
    this.scene.add(this.keyLight, this.fillLight, this.rimLight, this.camera);
  }

  private async loadGltf(source: string | Blob | ArrayBuffer, options: LoadOptions): Promise<GLTF> {
    const buffer = typeof source === "string" ? await this.fetchArrayBuffer(source, options) : source instanceof Blob ? await source.arrayBuffer() : source;
    return this.parseGltf(buffer);
  }

  private async parseGltf(buffer: ArrayBuffer): Promise<GLTF> {
    return await new Promise<GLTF>((resolve, reject) => {
      this.loader.parse(buffer, "", resolve, reject);
    });
  }

  private async fetchArrayBuffer(url: string, options: LoadOptions): Promise<ArrayBuffer> {
    const response = await fetch(url, { signal: options.signal });
    if (!response.ok) throw new Error(`Unable to load model (${response.status}).`);
    const total = Number(response.headers.get("content-length")) || undefined;
    const reader = response.body?.getReader();
    if (!reader) return response.arrayBuffer();
    const chunks: Uint8Array[] = []; let loaded = 0;
    while (true) { const { done, value } = await reader.read(); if (done) break; if (value) { chunks.push(value); loaded += value.byteLength; options.onProgress?.(loaded, total); } }
    const result = new Uint8Array(loaded); let offset = 0;
    chunks.forEach((chunk) => { result.set(chunk, offset); offset += chunk.byteLength; });
    return result.buffer;
  }

  private async loadMatchingBom(source: string | Blob | ArrayBuffer): Promise<void> {
    // Browser File/Blob inputs intentionally cannot read neighbouring local
    // files. Modelryx production assets are URL-backed, where the matching
    // sidecar can be requested safely and automatically.
    if (typeof source !== "string") return;
    const url = new URL(source, window.location.href);
    if (!url.pathname.toLowerCase().endsWith(".glb")) return;
    url.pathname = `${url.pathname.slice(0, -4)}.model-preview.json`;
    try { await this.loadBom(url.toString()); } catch { /* A BOM sidecar is optional. */ }
  }

  private async loadPfg(source: string | Blob | ArrayBuffer, options: LoadOptions): Promise<{ glb: ArrayBuffer; bom: string } | undefined> {
    if (!this.isPfgSource(source)) return undefined;
    const bytes = new Uint8Array(typeof source === "string" ? await this.fetchArrayBuffer(source, options) : source instanceof Blob ? await source.arrayBuffer() : source);
    let files: Record<string, Uint8Array>;
    try { files = unzipSync(bytes); } catch { throw new Error("The .pfg package is not a valid ZIP archive."); }
    const manifestBytes = files["manifest.json"];
    if (!manifestBytes) throw new Error("The .pfg package is missing manifest.json.");
    let manifest: unknown;
    try { manifest = JSON.parse(new TextDecoder().decode(manifestBytes)); } catch { throw new Error("The .pfg manifest is not valid JSON."); }
    if (!this.isPfgManifest(manifest)) throw new Error("The .pfg manifest is invalid or uses an unsupported format version.");
    const glb = files[manifest.model];
    const bom = files[manifest.modelPreview];
    if (!glb || !bom) throw new Error("The .pfg package is missing its declared model or preview file.");
    return { glb: glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength) as ArrayBuffer, bom: new TextDecoder().decode(bom) };
  }

  private isPfgSource(source: string | Blob | ArrayBuffer): boolean {
    if (typeof source === "string") return new URL(source, window.location.href).pathname.toLowerCase().endsWith(".pfg");
    if (source instanceof File && source.name.toLowerCase().endsWith(".pfg")) return true;
    if (source instanceof Blob) {
      const mediaType = source.type.split(";", 1)[0].trim().toLowerCase();
      return ["application/vnd.modelryx.graphics+zip", "application/zip"].includes(mediaType);
    }
    return false;
  }

  private parseBom(text: string): ModelryxBom {
    let value: unknown;
    try { value = JSON.parse(text); } catch { throw new Error("The BOM sidecar is not valid JSON."); }
    if (!this.isBom(value)) throw new Error("The BOM sidecar is missing schemaVersion, rootOccurrenceId, or valid items.");
    return value;
  }

  private setBom(bom: ModelryxBom): ModelryxBom {
    const navigationBom = {
      ...bom,
      items: bom.items.filter((item) => this.isNavigationItem(item)),
    };
    this.bom = navigationBom;
    this.buildJointMarkers(navigationBom);
    this.applyBomDisplay(navigationBom);
    this.emit("bomLoaded", { itemCount: navigationBom.items.length });
    return navigationBom;
  }

  private isNavigationItem(item: ModelryxBomItem): boolean {
    // FreeCAD assigns names such as Origin, Origin001, and Origin002 to its
    // internal coordinate-system containers. They have no final geometry and
    // are implementation details rather than useful viewer navigation nodes.
    if (/^origin\d*$/i.test(item.name.trim())) return false;
    return item.nodeType !== "reference";
  }

  private applyBomDisplay(bom: ModelryxBom): void {
    bom.items.forEach((item) => {
      if (item.renderOccurrenceIds?.length) {
        item.renderOccurrenceIds.forEach((renderOccurrenceId) => {
          (this.occurrenceIndex.get(renderOccurrenceId) ?? []).forEach((object) => {
            if (object instanceof THREE.Mesh) this.applyModelryxDisplay(object, item);
          });
        });
        return;
      }
      const renderOccurrenceId = item.renderOccurrenceId ?? item.occurrenceId;
      // FreeCAD Body history entries (Pad, Pocket, Pattern, ...) intentionally
      // point at the Body's final mesh for selection. Their own visibility is
      // usually false, however, and must not hide that final mesh.
      if (renderOccurrenceId !== item.occurrenceId) return;
      (this.occurrenceIndex.get(renderOccurrenceId) ?? []).forEach((object) => {
        if (object instanceof THREE.Mesh) this.applyModelryxDisplay(object, item);
      });
    });
    this.applyRenderMode();
    this.invalidate();
  }

  private buildJointMarkers(bom: ModelryxBom): void {
    this.jointMarkers.clear();
    const bounds = new THREE.Box3().setFromObject(this.modelRoot);
    const markerSize = Math.max(bounds.getSize(new THREE.Vector3()).length() * 0.025, 0.003);
    bom.items.filter((item) => item.nodeType === "joint" && item.placement).forEach((item) => {
      const marker = new THREE.Group();
      marker.name = `joint-${item.occurrenceId}`;
      marker.position.fromArray(item.placement!.position);
      if (item.placement!.rotation) marker.quaternion.fromArray(item.placement!.rotation);
      marker.visible = item.display?.visible ?? true;
      const axes = new THREE.AxesHelper(markerSize);
      const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 25 }, (_, index) => new THREE.Vector3(Math.cos(index / 24 * Math.PI * 2) * markerSize * 0.38, Math.sin(index / 24 * Math.PI * 2) * markerSize * 0.38, 0)),
      ), new THREE.LineBasicMaterial({ color: 0xffc107 }));
      marker.add(axes, ring);
      this.jointMarkers.add(marker);
      this.occurrenceIndex.set(item.occurrenceId, [marker]);
      const parts = this.partIndex.get(item.partId) ?? [];
      parts.push(marker); this.partIndex.set(item.partId, parts);
    });
  }

  private indexParts(root: THREE.Object3D): void {
    const visit = (object: THREE.Object3D, inherited?: ModelryxMetadata): void => {
      const metadata = this.getMetadata(object) ?? inherited;
      if (object instanceof THREE.Mesh && metadata) {
        object.userData[SELECTABLE] = true;
        this.applyModelryxDisplay(object, metadata);
        const partObjects = this.partIndex.get(metadata.partId) ?? [];
        partObjects.push(object); this.partIndex.set(metadata.partId, partObjects);
        if (metadata.occurrenceId) {
          const occurrenceObjects = this.occurrenceIndex.get(metadata.occurrenceId) ?? [];
          occurrenceObjects.push(object); this.occurrenceIndex.set(metadata.occurrenceId, occurrenceObjects);
        }
      }
      object.children.forEach((child) => visit(child, metadata));
    };
    visit(root);
  }

  private getMetadata(object: THREE.Object3D): ModelryxMetadata | undefined {
    const raw = object.userData?.modelryx;
    return raw && typeof raw.partId === "string" ? raw as ModelryxMetadata : undefined;
  }

  private applyModelryxDisplay(mesh: THREE.Mesh, metadata: ModelryxMetadata): void {
    const display = metadata.display;
    if (!display) return;
    // glTF exporters commonly reuse one material instance across many meshes.
    // CAD Body display color is per occurrence, so clone once before changing
    // color/opacity or the last processed Body would recolor every mesh.
    if (!this.resolvedAppearanceMeshes.has(mesh)) {
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map((material) => material.clone()) : mesh.material.clone();
      this.resolvedAppearanceMeshes.add(mesh);
    }
    mesh.visible = display.visible ?? mesh.visible;
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    materials.forEach((material) => {
      const cadMaterial = material as THREE.MeshStandardMaterial;
      if (display.color && /^#[0-9a-f]{6}$/i.test(display.color)) {
        cadMaterial.color.set(display.color);
        // The direct converter also embeds vertex colours. Those are useful for
        // generic GLB tools, but would multiply the Modelryx Body colour.
        cadMaterial.vertexColors = false;
      }
      if (typeof display.opacity === "number") {
        cadMaterial.opacity = THREE.MathUtils.clamp(display.opacity, 0, 1);
        cadMaterial.transparent = cadMaterial.opacity < 1;
        cadMaterial.depthWrite = cadMaterial.opacity === 1;
      }
      cadMaterial.needsUpdate = true;
    });
  }

  private styleComparisonRoot(root: THREE.Object3D, color: number, opacity: number): void {
    root.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = (Array.isArray(object.material) ? object.material : [object.material]).map(
        (source) => {
          const material = source.clone() as THREE.MeshStandardMaterial;
          material.color.setHex(color);
          material.vertexColors = false;
          material.transparent = true;
          material.opacity = opacity;
          material.depthWrite = false;
          return material;
        },
      );
      object.material = materials.length === 1 ? materials[0] : materials;
    });
  }

  private countParts(root: THREE.Object3D): number {
    const partIds = new Set<string>();
    root.traverse((object) => {
      const metadata = this.getMetadata(object);
      if (metadata) partIds.add(metadata.partId);
    });
    return partIds.size;
  }

  private applyRenderMode(): void {
    this.modelRoot.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const mesh = object as THREE.Mesh;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      materials.forEach((material) => {
        const renderMaterial = material as THREE.MeshStandardMaterial;
        const source = this.sourceMaterialState.get(material) ?? {
          opacity: renderMaterial.opacity,
          transparent: renderMaterial.transparent,
          depthWrite: renderMaterial.depthWrite,
        };
        this.sourceMaterialState.set(material, source);
        renderMaterial.wireframe = this.renderMode === "wireframe";
        const viewerOpacity = 1 - this.transparency / 100;
        renderMaterial.transparent = source.transparent || this.transparency > 0;
        renderMaterial.opacity = source.opacity * viewerOpacity;
        renderMaterial.depthWrite = this.transparency === 0 ? source.depthWrite : false;
        renderMaterial.needsUpdate = true;
      });
      const existing = mesh.getObjectByName(EDGE_OBJECT);
      if (this.renderMode === "shaded-edges") {
        if (!existing) this.addEdges(mesh);
        mesh.getObjectByName(EDGE_OBJECT)!.visible = true;
      } else if (existing) existing.visible = false;
    });
  }

  private addEdges(mesh: THREE.Mesh): void {
    const edgeGeometry = this.edgeGeometry(mesh);
    const edges = new THREE.LineSegments(edgeGeometry, new THREE.LineBasicMaterial({ color: 0x29313a }));
    edges.name = EDGE_OBJECT;
    edges.renderOrder = 1;
    mesh.add(edges);
  }

  private edgeGeometry(mesh: THREE.Mesh): THREE.BufferGeometry {
    const segments = this.findMetadata(mesh)?.edgeSegments;
    if (segments && segments.length >= 6 && segments.length % 6 === 0
      && segments.every((value) => Number.isFinite(value))) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(segments, 3));
      return geometry;
    }
    return new THREE.EdgesGeometry(mesh.geometry, this.edgeThresholdAngle);
  }

  private setVisible(partIds: string[], visible: boolean): void {
    partIds.forEach((id) => (this.partIndex.get(id) ?? []).forEach((object) => { object.visible = visible; }));
    this.rebuildSelectionOverlays();
    this.invalidate();
  }

  private setOccurrenceVisible(occurrenceId: string, visible: boolean): void {
    this.objectsForOccurrence(occurrenceId).forEach((object) => { object.visible = visible; });
    this.rebuildSelectionOverlays();
    this.invalidate();
  }

  private objectsForOccurrence(occurrenceId: string): THREE.Object3D[] {
    const item = this.getBomItem(occurrenceId);
    const renderOccurrenceIds = item?.renderOccurrenceIds?.length
      ? item.renderOccurrenceIds
      : [item?.renderOccurrenceId ?? occurrenceId];
    const objects = new Set<THREE.Object3D>();
    renderOccurrenceIds.forEach((renderOccurrenceId) => {
      (this.occurrenceIndex.get(renderOccurrenceId) ?? []).forEach((object) => objects.add(object));
    });

    // Some native CAD formats expose logical component occurrences in the BOM
    // while attaching renderable solids below those occurrences. AutoCAD, for
    // example, can navigate with `model/335/328` and render its AcDb3dSolid as
    // `model/335/328/2F4`. Bind the component to every indexed descendant so
    // tree selection, visibility, isolation, and fit-selection operate on its
    // geometry without requiring solid implementation details in navigation.
    const descendantPrefix = `${occurrenceId}/`;
    this.occurrenceIndex.forEach((indexedObjects, indexedOccurrenceId) => {
      if (!indexedOccurrenceId.startsWith(descendantPrefix)) return;
      indexedObjects.forEach((object) => objects.add(object));
    });
    if (!this.bom) return [...objects];

    const childrenByParent = new Map<string, ModelryxBomItem[]>();
    this.bom.items.forEach((candidate) => {
      if (!candidate.parentOccurrenceId) return;
      const children = childrenByParent.get(candidate.parentOccurrenceId) ?? [];
      children.push(candidate);
      childrenByParent.set(candidate.parentOccurrenceId, children);
    });
    const children = childrenByParent.get(occurrenceId) ?? [];
    if (item?.nodeType !== "assembly" && children.length === 0) return [...objects];

    // An assembly may have a directly indexed STEP/glTF container or even a
    // coarse mesh as well as separately rendered child occurrences. Returning
    // early for that direct object left part of the assembly unselected. Union
    // the direct binding with every rendered descendant for all group actions.
    const pending = [...children];
    while (pending.length) {
      const child = pending.pop()!;
      const renderIds = child.renderOccurrenceIds?.length
        ? child.renderOccurrenceIds
        : [child.renderOccurrenceId ?? child.occurrenceId];
      renderIds.forEach((renderId) => {
        (this.occurrenceIndex.get(renderId) ?? []).forEach((object) => objects.add(object));
      });
      pending.push(...(childrenByParent.get(child.occurrenceId) ?? []));
    }
    return [...objects];
  }

  private setSelection(partIds: string[], source: "viewer" | "api"): void {
    const ids = partIds.filter((id) => this.partIndex.has(id));
    this.setSelectionObjects(ids.flatMap((id) => this.partIndex.get(id) ?? []), ids, source);
  }

  selectOccurrences(occurrenceIds: string[], source: "viewer" | "api" = "api"): void {
    const ids = [...new Set(occurrenceIds)].filter(
      (id) => Boolean(this.getBomItem(id)) || this.occurrenceIndex.has(id),
    );
    const objects = ids.flatMap((id) => this.objectsForOccurrence(id));
    const partIds = ids.map((id) => this.getBomItem(id)?.partId).filter((id): id is string => Boolean(id));
    this.setSelectionObjects(objects, partIds, source, ids);
  }

  private setSelectionObjects(objects: THREE.Object3D[], partIds: string[], source: "viewer" | "api", occurrenceIds?: string[]): void {
    this.selected.clear();
    partIds.forEach((id) => this.selected.add(id));
    this.selectedOccurrenceIds.clear();
    (occurrenceIds ?? objects.map((object) => this.findMetadata(object)?.occurrenceId).filter((id): id is string => Boolean(id))).forEach((id) => this.selectedOccurrenceIds.add(id));
    this.selectedObjects = [...new Set(objects)];
    this.rebuildSelectionOverlays();
    this.rebuildHoverOverlay();
    this.emit("selectionChanged", { partIds: [...this.selected], occurrenceIds: [...this.selectedOccurrenceIds], source });
    this.invalidate();
  }

  private rebuildSelectionOverlays(): void {
    this.disposeOverlayGeometry(this.selectionOverlays);
    this.selectionOverlays.clear();
    this.selectedObjects.filter((object): object is THREE.Mesh => object.visible && object instanceof THREE.Mesh).forEach((mesh) => {
      const overlay = new THREE.Mesh(mesh.geometry, this.selectionMaterial);
      overlay.matrixAutoUpdate = false; overlay.matrix.copy(mesh.matrixWorld);
      this.selectionOverlays.add(overlay);
      const edges = new THREE.LineSegments(this.edgeGeometry(mesh), this.selectionEdgeMaterial);
      edges.matrixAutoUpdate = false; edges.matrix.copy(mesh.matrixWorld);
      this.selectionOverlays.add(edges);
    });
  }

  private fitObject(value: THREE.Object3D | THREE.Object3D[]): void {
    const box = new THREE.Box3();
    (Array.isArray(value) ? value : [value]).forEach((object) => box.expandByObject(object));
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.01);
    const distance = radius / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.25;
    // Keep the user's current viewing direction. Fit is a framing action, not
    // a request to reset the camera to the default isometric orientation.
    const direction = this.camera.position.clone().sub(this.controls.target);
    if (direction.lengthSq() < Number.EPSILON) direction.set(1, 0.75, 1);
    direction.normalize();
    this.controls.target.copy(center);
    this.camera.position.copy(direction.multiplyScalar(distance).add(center));
    this.camera.near = Math.max(distance / 1000, 0.001); this.camera.far = distance * 100;
    this.camera.updateProjectionMatrix(); this.controls.update(); this.invalidate();
  }

  private isVisibleInScene(object: THREE.Object3D): boolean {
    for (let current: THREE.Object3D | null = object; current; current = current.parent) {
      if (!current.visible) return false;
    }
    return true;
  }

  private createGradientBackground(top: number, bottom: number): THREE.Texture {
    const canvas = document.createElement("canvas"); canvas.width = 2; canvas.height = 256;
    const context = canvas.getContext("2d")!; const gradient = context.createLinearGradient(0, 0, 0, canvas.height);
    gradient.addColorStop(0, `#${top.toString(16).padStart(6, "0")}`); gradient.addColorStop(1, `#${bottom.toString(16).padStart(6, "0")}`);
    context.fillStyle = gradient; context.fillRect(0, 0, canvas.width, canvas.height);
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    const previous = this.scene.background; this.scene.background = texture;
    if (previous instanceof THREE.Texture) previous.dispose();
    return texture;
  }

  private resize(): void {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false); this.invalidate();
  }

  private async waitForLayout(): Promise<void> {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      this.resize();
      const { width, height } = this.container.getBoundingClientRect();
      if (width > 1 && height > 1) return;
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }
  }

  private async commitInitialPresentation(): Promise<void> {
    // Give the host UI one layout cycle to mount the BOM tree/sidebar, then
    // draw at the final canvas size before removing its loading overlay.
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    this.resize();
    this.renderNow();
  }

  private invalidate = (): void => {
    if (this.frameRequested || this.disposed) return;
    this.frameRequested = true;
    requestAnimationFrame(() => { this.frameRequested = false; this.renderNow(); });
  };

  private renderNow(): void {
    if (this.disposed) return;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.measurementLabels.forEach(({ position, element }) => {
      const projected = position.clone().project(this.camera);
      element.hidden = projected.z < -1 || projected.z > 1;
      element.style.left = `${(projected.x * 0.5 + 0.5) * rect.width}px`;
      element.style.top = `${(-projected.y * 0.5 + 0.5) * rect.height}px`;
    });
  }

  private renderAfterLayoutSettles(): void {
    const generation = ++this.loadRenderGeneration;
    let remainingFrames = 3;
    const render = (): void => {
      if (this.disposed || generation !== this.loadRenderGeneration) return;
      this.resize();
      this.renderNow();
      remainingFrames -= 1;
      if (remainingFrames > 0) requestAnimationFrame(render);
    };
    requestAnimationFrame(render);
    // Firefox can complete WebGL drawing before the grid reflow caused by the
    // newly-visible BOM panel reaches the canvas. These delayed redraws happen
    // only after a model load and avoid an always-running animation loop.
    [50, 180, 450].forEach((delay) => {
      const timer = window.setTimeout(() => {
        this.delayedRenderTimers.delete(timer);
        if (this.disposed || generation !== this.loadRenderGeneration) return;
        this.resize();
        this.renderNow();
      }, delay);
      this.delayedRenderTimers.add(timer);
    });
  }

  private warmUpAfterLoad(): void {
    const generation = ++this.loadRenderGeneration;
    const deadline = performance.now() + 1_000;
    const render = (): void => {
      if (this.disposed || generation !== this.loadRenderGeneration) return;
      this.resize();
      this.renderNow();
      if (performance.now() < deadline) requestAnimationFrame(render);
    };
    requestAnimationFrame(render);
  }

  private handleContextRestored = (): void => {
    this.resize();
    this.renderAfterLayoutSettles();
    this.warmUpAfterLoad();
  };

  private handlePointerDown = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    const hit = this.pick(event.clientX, event.clientY);
    if (this.measurementActive) {
      if (hit) {
        if (this.measurementType === "edge") this.addEdgeMeasurement(hit);
        else this.addMeasurementPoint(hit.point);
      }
      return;
    }
    const metadata = hit ? this.findMetadata(hit.object) : undefined;
    if (!metadata?.occurrenceId) {
      this.setSelection([], "viewer");
      return;
    }
    if (event.ctrlKey || event.metaKey) this.toggleOccurrence(metadata.occurrenceId, "viewer");
    else this.selectOccurrences([metadata.occurrenceId], "viewer");
  };

  private handlePointerMove = (event: PointerEvent): void => {
    if (this.measurementActive) return;
    if (event.buttons !== 0) {
      this.clearHover();
      return;
    }
    const hit = this.pick(event.clientX, event.clientY);
    const mesh = hit?.object instanceof THREE.Mesh ? hit.object : undefined;
    const metadata = mesh ? this.findMetadata(mesh) : undefined;
    if (!mesh || !metadata) {
      this.clearHover();
      return;
    }
    const occurrenceId = metadata.occurrenceId;
    const item = occurrenceId ? this.getBomItem(occurrenceId) : undefined;
    if (this.hoveredObject !== mesh) {
      this.hoveredObject = mesh;
      this.hoveredOccurrenceId = occurrenceId;
      this.rebuildHoverOverlay();
    }
    this.renderer.domElement.style.cursor = "pointer";
    this.emit("hoverChanged", {
      partId: metadata.partId, occurrenceId, name: item?.name ?? mesh.name ?? metadata.partId,
      nodeType: item?.nodeType ?? metadata.nodeType, clientX: event.clientX, clientY: event.clientY,
    });
  };

  private handlePointerLeave = (): void => { this.clearHover(); };

  private rebuildHoverOverlay(): void {
    this.disposeOverlayGeometry(this.hoverOverlays);
    this.hoverOverlays.clear();
    const mesh = this.hoveredObject;
    if (!mesh || !this.isVisibleInScene(mesh) || (this.hoveredOccurrenceId && this.selectedOccurrenceIds.has(this.hoveredOccurrenceId))) return;
    const overlay = new THREE.Mesh(mesh.geometry, this.hoverMaterial);
    overlay.matrixAutoUpdate = false; overlay.matrix.copy(mesh.matrixWorld);
    const edges = new THREE.LineSegments(this.edgeGeometry(mesh), this.hoverEdgeMaterial);
    edges.matrixAutoUpdate = false; edges.matrix.copy(mesh.matrixWorld);
    this.hoverOverlays.add(overlay, edges);
    this.invalidate();
  }

  private clearHover(): void {
    if (!this.hoveredObject && !this.hoverOverlays.children.length) return;
    this.hoveredObject = undefined;
    this.hoveredOccurrenceId = undefined;
    this.disposeOverlayGeometry(this.hoverOverlays);
    this.hoverOverlays.clear();
    if (this.renderer) this.renderer.domElement.style.cursor = "";
    this.emit("hoverChanged", {});
    this.invalidate();
  }

  private disposeOverlayGeometry(group: THREE.Group): void {
    group.children.forEach((child) => { if (child instanceof THREE.LineSegments) child.geometry.dispose(); });
  }

  private pick(clientX: number, clientY: number): THREE.Intersection | undefined {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    return this.raycaster.intersectObjects(this.modelRoot.children, true).find((item) => item.object.userData[SELECTABLE] && this.isVisibleInScene(item.object));
  }

  private addMeasurementPoint(point: THREE.Vector3): void {
    const marker = new THREE.Mesh(this.measurementPointGeometry, this.measurementPointMaterial);
    marker.position.copy(point);
    marker.scale.setScalar(this.measurementMarkerSize);
    marker.renderOrder = 20;
    if (!this.measurementDraft) {
      this.measurementDraft = new THREE.Group();
      this.measurementRoot.add(this.measurementDraft);
    }
    this.measurementPoints.push(point.clone());
    this.measurementDraft.add(marker);
    const requiredPoints = this.measurementType === "distance" ? 2 : this.measurementType === "angle" ? 3 : 1;
    if (this.measurementPoints.length < requiredPoints) {
      this.emit("measurementChanged", { state: "point-picked", pointCount: this.measurementPoints.length, unit: this.measurementUnit, measurementType: this.measurementType });
      this.invalidate();
      return;
    }

    let value: number | THREE.Vector3;
    let labelPosition: THREE.Vector3;
    if (this.measurementType === "distance") {
      const [first, second] = this.measurementPoints;
      value = first.distanceTo(second);
      labelPosition = first.clone().add(second).multiplyScalar(0.5);
      this.addMeasurementLine(this.measurementDraft, [first, second]);
    } else if (this.measurementType === "angle") {
      const [first, vertex, third] = this.measurementPoints;
      value = THREE.MathUtils.radToDeg(first.clone().sub(vertex).angleTo(third.clone().sub(vertex)));
      labelPosition = vertex.clone();
      this.addMeasurementLine(this.measurementDraft, [first, vertex, third]);
    } else {
      value = point.clone();
      labelPosition = point.clone();
    }
    const label = document.createElement("span");
    label.className = "measurement-label";
    const formattedValue = this.formatMeasurement(this.measurementType, value);
    label.textContent = formattedValue;
    this.container.append(label);
    this.measurementLabels.push({ position: labelPosition, kind: this.measurementType, value, element: label });
    this.measurementDraft = undefined;
    this.measurementPoints.length = 0;
    this.measurementActive = false;
    this.renderer.domElement.style.cursor = "";
    this.emit("measurementChanged", {
      state: "completed", pointCount: requiredPoints, unit: this.measurementUnit, measurementType: this.measurementType,
      distance: this.measurementType === "distance" ? value as number : undefined,
      formattedDistance: this.measurementType === "distance" ? formattedValue : undefined, formattedValue,
    });
    this.invalidate();
  }

  private addEdgeMeasurement(hit: THREE.Intersection): void {
    if (!(hit.object instanceof THREE.Mesh) || !hit.face) return;
    const positions = hit.object.geometry.getAttribute("position");
    if (!positions) return;
    const vertices = [hit.face.a, hit.face.b, hit.face.c].map((index) => new THREE.Vector3().fromBufferAttribute(positions, index).applyMatrix4(hit.object.matrixWorld));
    const candidates: Array<[THREE.Vector3, THREE.Vector3]> = [[vertices[0], vertices[1]], [vertices[1], vertices[2]], [vertices[2], vertices[0]]];
    let edge = candidates[0];
    let nearestDistance = Number.POSITIVE_INFINITY;
    candidates.forEach((candidate) => {
      const closest = new THREE.Line3(candidate[0], candidate[1]).closestPointToPoint(hit.point, true, new THREE.Vector3());
      const distance = closest.distanceToSquared(hit.point);
      if (distance < nearestDistance) { nearestDistance = distance; edge = candidate; }
    });

    const group = new THREE.Group();
    edge.forEach((point) => {
      const marker = new THREE.Mesh(this.measurementPointGeometry, this.measurementPointMaterial);
      marker.position.copy(point); marker.scale.setScalar(this.measurementMarkerSize); marker.renderOrder = 20; group.add(marker);
    });
    this.addMeasurementLine(group, edge);
    this.measurementRoot.add(group);
    const distance = edge[0].distanceTo(edge[1]);
    const position = edge[0].clone().add(edge[1]).multiplyScalar(0.5);
    const label = document.createElement("span");
    label.className = "measurement-label";
    label.textContent = this.formatMeasurement("edge", distance);
    this.container.append(label);
    this.measurementLabels.push({ position, kind: "edge", value: distance, element: label });
    this.measurementActive = false;
    this.renderer.domElement.style.cursor = "";
    this.emit("measurementChanged", { state: "completed", pointCount: 1, distance, unit: this.measurementUnit, measurementType: "edge", formattedValue: this.formatDistance(distance) });
    this.invalidate();
  }

  private addMeasurementLine(group: THREE.Group, points: THREE.Vector3[]): void {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), this.measurementLineMaterial);
    line.renderOrder = 20;
    group.add(line);
  }

  private formatMeasurement(kind: MeasurementType, value: number | THREE.Vector3): string {
    if (kind === "angle") return `${(value as number).toFixed(2)}°`;
    if (kind === "point") {
      const point = value as THREE.Vector3;
      return `X ${this.formatDistance(point.x)} · Y ${this.formatDistance(point.y)} · Z ${this.formatDistance(point.z)}`;
    }
    return this.formatDistance(value as number);
  }

  private formatDistance(distance: number): string {
    const scales: Record<MeasurementUnit, number> = { mm: 1000, cm: 100, m: 1, in: 39.3700787402 };
    const value = distance * scales[this.measurementUnit];
    const precision = value >= 100 ? 1 : value >= 10 ? 2 : 3;
    return `${value.toFixed(precision)} ${this.measurementUnit}`;
  }

  private handleCanvasContextMenu = (event: MouseEvent): void => {
    event.preventDefault();
    const hit = this.pick(event.clientX, event.clientY);
    const occurrenceId = hit ? this.findMetadata(hit.object)?.occurrenceId : undefined;
    if (occurrenceId && !this.selectedOccurrenceIds.has(occurrenceId)) {
      this.selectOccurrences([occurrenceId], "viewer");
    }
    this.container.dispatchEvent(new CustomEvent("modelryx-contextmenu", { detail: { x: event.clientX, y: event.clientY } }));
  };

  private disposeObject(object: THREE.Object3D): void {
    object.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return;
      const mesh = child as THREE.Mesh;
      mesh.geometry.dispose();
      (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((material) => material.dispose());
    });
  }

  private findMetadata(object: THREE.Object3D): ModelryxMetadata | undefined {
    let current: THREE.Object3D | null = object;
    while (current) {
      const metadata = this.getMetadata(current);
      if (metadata) return metadata;
      current = current.parent;
    }
    return undefined;
  }

  private isBom(value: unknown): value is ModelryxBom {
    if (!value || typeof value !== "object") return false;
    const bom = value as Partial<ModelryxBom>;
    return typeof bom.schemaVersion === "number"
      && typeof bom.rootOccurrenceId === "string"
      && Array.isArray(bom.items)
      && bom.items.every((item) => item && typeof item.partId === "string" && typeof item.occurrenceId === "string" && typeof item.name === "string");
  }

  private isPfgManifest(value: unknown): value is { format: "Modelryx Graphics"; version: 1; model: string; modelPreview: string } {
    if (!value || typeof value !== "object") return false;
    const manifest = value as { format?: unknown; version?: unknown; model?: unknown; modelPreview?: unknown };
    return manifest.format === "Modelryx Graphics" && manifest.version === 1 && typeof manifest.model === "string" && typeof manifest.modelPreview === "string";
  }

  private emit<K extends ViewerEventName>(event: K, payload: ViewerEvents[K]): void { this.listeners.get(event)?.forEach((listener) => (listener as Listener<ViewerEvents[K]>)(payload)); }
  private assertActive(): void { if (this.disposed) throw new Error("Viewer has been disposed."); }
  private normalizeTransparency(value: number): number { return THREE.MathUtils.clamp(value, 0, 100); }
}
