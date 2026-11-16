export type DisplayProfileName = 'cad' | 'technical' | 'studio';
export type RenderMode = 'shaded' | 'shaded-edges' | 'wireframe';
export type StandardView = 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom' | 'iso';
export type MeasurementUnit = 'mm' | 'cm' | 'm' | 'in';
export type MeasurementType = 'distance' | 'edge' | 'angle' | 'point';
export type ComparisonMode = 'from' | 'to' | 'overlay';

export interface ModelryxMetadata {
  partId: string;
  occurrenceId?: string;
  parentOccurrenceId?: string;
  nodeType?: 'assembly' | 'body' | 'part' | 'mesh' | 'feature' | 'reference' | 'joint';
  cadType?: string;
  renderOccurrenceId?: string;
  /** One or more rendered GLB occurrences represented by this navigation item. */
  renderOccurrenceIds?: string[];
  display?: {
    color?: string;
    opacity?: number;
    visible?: boolean;
  };
  placement?: { position: [number, number, number]; rotation?: [number, number, number, number] };
  revision?: string;
  source?: string | { system?: string; objectName?: string; objectLabel?: string; typeId?: string };
  sourceObjectId?: string;
  material?: string;
  finish?: string;
  displayColor?: string;
  /** Native CAD B-Rep edges as local-space XYZ line-segment pairs. */
  edgeSegments?: number[];
}

export interface ModelryxViewerOptions {
  displayProfile?: DisplayProfileName;
  renderMode?: RenderMode;
  edgeThresholdAngle?: number;
  maxPixelRatio?: number;
  transparency?: number;
}

export interface LoadOptions {
  signal?: AbortSignal;
  onProgress?: (loaded: number, total?: number) => void;
}

export interface SelectionChangedEvent {
  partIds: string[];
  occurrenceIds: string[];
  source: 'viewer' | 'api';
}

export interface HoverChangedEvent {
  partId?: string;
  occurrenceId?: string;
  name?: string;
  nodeType?: ModelryxMetadata['nodeType'];
  clientX?: number;
  clientY?: number;
}

export interface MeasurementChangedEvent {
  state: 'started' | 'point-picked' | 'completed' | 'cancelled' | 'cleared';
  pointCount?: number;
  distance?: number;
  unit?: MeasurementUnit;
  formattedDistance?: string;
  measurementType?: MeasurementType;
  formattedValue?: string;
}

export interface ModelryxBomItem extends ModelryxMetadata {
  occurrenceId: string;
  name: string;
  properties?: Record<string, unknown>;
}

export interface ModelryxBom {
  schemaVersion: number;
  rootOccurrenceId: string;
  items: ModelryxBomItem[];
}

export interface ViewerEvents {
  selectionChanged: SelectionChangedEvent;
  hoverChanged: HoverChangedEvent;
  measurementChanged: MeasurementChangedEvent;
  loadError: Error;
  loaded: { partCount: number };
  bomLoaded: { itemCount: number };
  bomCleared: undefined;
  loadingChanged: { loading: boolean };
  comparisonLoaded: { fromPartCount: number; toPartCount: number; mode: ComparisonMode };
}

export type ViewerEventName = keyof ViewerEvents;
