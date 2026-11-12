import { useEffect, useState } from 'react';
import type { Item } from '../../api';
const dateTime = (value?: string | null) => (value ? new Date(value).toLocaleString() : '—');
const displayMetadataValue = (value: unknown): string =>
  value === null || value === undefined
    ? '—'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);
const formatPropertyName = (name: string) =>
  name.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase());

type DrawingDetailKind =
  'sheets' | 'layers' | 'dimensions' | 'annotations' | 'title-blocks' | 'notes';

export const previewNodeDisplayName = (name: string) => {
  const parts = name.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : name;
};

type LimitationDetail = {
  title: string;
  affectedData: string[];
  diagnostic: string;
};

const limitationDetail = (diagnostic: string): LimitationDetail => {
  const rules: Array<[RegExp, (match: RegExpMatchArray) => Omit<LimitationDetail, 'diagnostic'>]> =
    [
      [
        /^Entity\s+(\S+)\s+has no geometric extents/i,
        (match) => ({
          title: `Entity ${match[1]} geometry`,
          affectedData: [`Native entity ${match[1]}`, 'Geometry → bounding box'],
        }),
      ],
      [
        /^Solid\s+(\S+)\s+mass properties failed/i,
        (match) => ({
          title: `Solid ${match[1]} physical properties`,
          affectedData: [
            `Native solid ${match[1]}`,
            'Physical → volume, surface area, mass and density',
          ],
        }),
      ],
      [
        /^Block\s+(\S+)\s+attributes could not be read/i,
        (match) => ({
          title: `Block ${match[1]} attributes`,
          affectedData: [`Native block ${match[1]}`, 'Engineering properties → block attributes'],
        }),
      ],
      [
        /^Block\s+(\S+)\s+dynamic properties could not be read/i,
        (match) => ({
          title: `Block ${match[1]} dynamic properties`,
          affectedData: [
            `Native block ${match[1]}`,
            'Engineering properties → dynamic block parameters',
          ],
        }),
      ],
      [
        /^Block\s+(\S+)\s+contains a cyclic definition reference/i,
        (match) => ({
          title: `Block ${match[1]} component structure`,
          affectedData: [`Native block ${match[1]}`, 'Assembly → occurrence hierarchy and counts'],
        }),
      ],
      [
        /parameters? (?:unavailable|incomplete|could not be read)/i,
        () => ({
          title: 'Inventor parameters',
          affectedData: ['Native → parameters', 'Engineering properties derived from parameters'],
        }),
      ],
      [
        /iProperties incomplete/i,
        () => ({
          title: 'Inventor iProperties',
          affectedData: [
            'Identity → part number, revision and description',
            'Engineering properties → iProperties',
          ],
        }),
      ],
      [
        /assembly occurrences? incomplete/i,
        () => ({
          title: 'Assembly occurrences',
          affectedData: [
            'Assembly → instance and component counts',
            'Native → occurrence hierarchy',
          ],
        }),
      ],
      [
        /BOM view incomplete/i,
        () => ({
          title: 'Assembly BOM',
          affectedData: ['Native → BOM views', 'Assembly → BOM rows'],
        }),
      ],
      [
        /no surface or solid bodies/i,
        () => ({
          title: 'Solid-body geometry and physical properties',
          affectedData: [
            'Geometry → volume and surface area',
            'Physical → material, mass and density',
            '3D preview → solid surfaces',
          ],
        }),
      ],
      [
        /no 3D solids were found/i,
        () => ({
          title: '3D solid geometry',
          affectedData: [
            'Geometry → solid count, volume and surface area',
            'Physical → mass and density',
            '3D preview → solid geometry',
          ],
        }),
      ],
      [
        /complete mass properties/i,
        () => ({
          title: 'Solid mass properties',
          affectedData: ['Physical → mass and density', 'Geometry → volume and surface area'],
        }),
      ],
      [
        /physical properties were unavailable/i,
        () => ({
          title: 'Physical properties',
          affectedData: [
            'Physical → material, mass and density',
            'Geometry → volume and surface area',
          ],
        }),
      ],
      [
        /feature history is unavailable/i,
        () => ({
          title: 'Parametric feature history',
          affectedData: ['Feature summary → feature counts', 'Feature history → feature list'],
        }),
      ],
      [
        /drawing contains no published sheets/i,
        () => ({
          title: 'Drawing sheets',
          affectedData: ['Drawing → sheets, dimensions, annotations and title blocks'],
        }),
      ],
      [
        /drawing layouts could not be read/i,
        () => ({
          title: 'Drawing layouts',
          affectedData: ['Drawing → sheets and page details'],
        }),
      ],
      [
        /drawing summary properties could not be read/i,
        () => ({
          title: 'Drawing summary properties',
          affectedData: [
            'Identity → title and description',
            'Engineering properties → drawing summary',
          ],
        }),
      ],
      [
        /layer table could not be read/i,
        () => ({
          title: 'Drawing layers',
          affectedData: ['Drawing → layers'],
        }),
      ],
    ];
  for (const [pattern, create] of rules) {
    const match = diagnostic.match(pattern);
    if (match) return { ...create(match), diagnostic };
  }
  const prefix = diagnostic.split(':', 1)[0].trim();
  return {
    title: prefix.length <= 80 ? prefix : 'Metadata extraction limitation',
    affectedData: ['The worker did not identify a specific property or parameter'],
    diagnostic,
  };
};

export function PropertiesSection({
  item,
  metadata,
}: {
  item: Item;
  metadata: Record<string, unknown>;
}) {
  const [drawingDetail, setDrawingDetail] = useState<DrawingDetailKind>();
  const [featureDetail, setFeatureDetail] = useState<string>();
  const [limitationsOpen, setLimitationsOpen] = useState(false);
  const record = (value: unknown): Record<string, unknown> =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const nestedPartMetadata = record(metadata.partMetadata);
  const isNormalizedPartMetadata =
    typeof metadata.schema_version === 'string' &&
    Object.keys(record(metadata.identity)).length > 0 &&
    Object.keys(record(metadata.geometry)).length > 0;
  const partMetadata = Object.keys(nestedPartMetadata).length
    ? nestedPartMetadata
    : isNormalizedPartMetadata
      ? metadata
      : {};
  const identity = record(partMetadata.identity);
  const geometry = record(partMetadata.geometry);
  const boundingBox = record(geometry.bounding_box);
  const shape = record(geometry.shape);
  const topology = record(geometry.topology);
  const physical = record(partMetadata.physical);
  const material = record(physical.material);
  const features = record(partMetadata.features);
  const featureSummary = record(features.summary);
  const assembly = record(partMetadata.assembly);
  const quality = record(partMetadata.quality);
  const source = record(partMetadata.source);
  const drawings = Array.isArray(partMetadata.drawings) ? partMetadata.drawings.map(record) : [];
  const drawingSections = drawings.flatMap((drawing) =>
    Array.isArray(drawing.sections) ? drawing.sections.map(record) : []
  );
  const drawingEntities = drawings.flatMap((drawing) =>
    Array.isArray(drawing.entities) ? drawing.entities.map(record) : []
  );
  const drawingDimensions = drawingEntities.filter(
    (entity) => entity.type === 'DIMENSION' || entity.type === 'TOLERANCE'
  );
  const drawingAnnotations = drawingEntities.filter((entity) => entity.type === 'ANNOTATION');
  const titleBlockSections = drawingSections.filter((section) => section.kind === 'TITLE_BLOCK');
  const noteSections = drawingSections.filter((section) => section.kind === 'NOTES');
  const native = record(partMetadata.native);
  const sourceDocument = record(native.source_document);
  const nativeTitleBlock = record(native.title_block);
  const drawingSheets = Array.isArray(native.drawing_sheets)
    ? native.drawing_sheets.map(record)
    : [];
  const customProperties = {
    ...record(partMetadata.properties),
  };
  const structuredArray = (value: unknown): Record<string, unknown>[] => {
    if (Array.isArray(value))
      return value.map(record).filter((entry) => Object.keys(entry).length > 0);
    if (typeof value !== 'string') return [];
    try {
      const parsed: unknown = JSON.parse(value);
      return Array.isArray(parsed)
        ? parsed.map(record).filter((entry) => Object.keys(entry).length > 0)
        : [];
    } catch {
      return [];
    }
  };
  const nativeLayers = structuredArray(native.layers);
  const drawingLayers =
    nativeLayers.length > 0 ? nativeLayers : structuredArray(customProperties.layers);
  const featureItems = Array.isArray(features.items) ? features.items.map(record) : [];
  const warnings = Array.isArray(quality.warnings)
    ? quality.warnings.filter((value): value is string => typeof value === 'string')
    : [];
  const limitations = warnings.map(limitationDetail);
  const present = (value: unknown) => value !== null && value !== undefined && value !== '';
  const number = (value: unknown, unit: string, digits = 2) =>
    typeof value === 'number'
      ? `${new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value)} ${unit}`
      : '—';
  const rows = (values: Array<[string, unknown]>) => values.filter(([, value]) => present(value));
  const annotationRows = (value: unknown): Array<[string, unknown]> => {
    if (typeof value !== 'string') return [];
    return value
      .split(/\r?\n|\s{2,}/)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part, index) => {
        const separator = part.indexOf(':');
        return separator > 0
          ? [part.slice(0, separator).trim(), part.slice(separator + 1).trim()]
          : [index === 0 ? 'Text' : `Text ${index + 1}`, part];
      });
  };
  const identityRows = rows([
    ['CAD name', identity.display_name ?? identity.name],
    ['Description', identity.description],
    ['Type', item.item_type === 'ASSEMBLY' ? 'Assembly' : 'Part'],
    ['Part number', identity.part_number],
    ['CAD revision', identity.revision],
    [
      'Classification',
      Array.isArray(identity.classification) ? identity.classification.join(', ') : null,
    ],
    ['Tags', Array.isArray(identity.tags) ? identity.tags.join(', ') : null],
  ]);
  const geometryRows = rows([
    [
      'Overall size',
      [boundingBox.x_mm, boundingBox.y_mm, boundingBox.z_mm].every(
        (value) => typeof value === 'number'
      )
        ? `${number(boundingBox.x_mm, 'mm')} × ${number(boundingBox.y_mm, 'mm')} × ${number(boundingBox.z_mm, 'mm')}`
        : null,
    ],
    ['Volume', typeof shape.volume_mm3 === 'number' ? number(shape.volume_mm3, 'mm³') : null],
    [
      'Surface area',
      typeof shape.surface_area_mm2 === 'number' ? number(shape.surface_area_mm2, 'mm²') : null,
    ],
    ['Solids', topology.solid_count],
    ['Faces', topology.face_count],
    ['Edges', topology.edge_count],
  ]);
  const physicalRows = rows([
    ['Material', material.normalized_name ?? material.name],
    ['Mass', typeof physical.mass_kg === 'number' ? number(physical.mass_kg, 'kg', 4) : null],
    [
      'Density',
      typeof physical.density_kg_m3 === 'number' ? number(physical.density_kg_m3, 'kg/m³') : null,
    ],
  ]);
  const workbenchRequirements = structuredArray(
    native.workbench_requirements ?? customProperties.workbench_requirements
  );
  const requiredWorkbenchModules = workbenchRequirements.flatMap((requirement) =>
    Array.isArray(requirement.required_modules)
      ? requirement.required_modules.filter((value): value is string => typeof value === 'string')
      : []
  );
  const missingWorkbenchModules = workbenchRequirements.flatMap((requirement) =>
    Array.isArray(requirement.missing_modules)
      ? requirement.missing_modules.filter((value): value is string => typeof value === 'string')
      : []
  );
  const sourceCompatibilityRows = rows([
    ['Native format', sourceDocument.format],
    ['FCStd file version', sourceDocument.file_format_version],
    ['Document schema version', sourceDocument.document_schema_version],
    ['Saved by FreeCAD', sourceDocument.authoring_freecad_version],
    ['Processed by FreeCAD', source.system_version],
    ['Metadata worker', source.worker],
    ['Converter version', source.worker_version],
    ['Assembly model', native.assembly_model ?? customProperties.assembly_model],
    [
      'Required workbench modules',
      requiredWorkbenchModules.length > 0
        ? [...new Set(requiredWorkbenchModules)].join(', ')
        : null,
    ],
    [
      'Missing workbench modules',
      missingWorkbenchModules.length > 0 ? [...new Set(missingWorkbenchModules)].join(', ') : null,
    ],
  ]);
  const featureRows = rows(
    Object.entries(featureSummary)
      .filter(([, value]) => typeof value !== 'number' || value > 0)
      .map(([key, value]) => [formatPropertyName(key.replace(/_count$/, '')), value])
  );
  const featureHistory = new Map<string, Record<string, unknown>[]>();
  featureItems.forEach((feature, index) => {
    const properties = record(feature.properties);
    const sourceType = typeof properties.sourceType === 'string' ? properties.sourceType : '';
    const normalizedType = typeof feature.type === 'string' ? feature.type : 'UNKNOWN_FEATURE';
    const rawLabel = sourceType
      ? sourceType.replace(/^.*::/, '')
      : normalizedType === 'UNKNOWN_FEATURE'
        ? 'Other'
        : normalizedType;
    const label = formatPropertyName(rawLabel.replace(/([a-z])([A-Z])/g, '$1 $2'));
    const name =
      typeof feature.name === 'string' && feature.name.trim()
        ? feature.name.trim()
        : `Feature ${index + 1}`;
    featureHistory.set(label, [...(featureHistory.get(label) ?? []), { ...feature, name }]);
  });
  const featureHistoryRows = [...featureHistory.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([label, items]): [string, unknown] => [label, items.length]);
  const featureAvailabilityRows = rows([
    ['Availability', quality.features],
    ['Source', features.source],
  ]);
  const featureUnavailableMessage =
    quality.features === 'unsupported'
      ? String(source.system ?? '').toLocaleLowerCase() === 'step'
        ? 'STEP preserves final geometry but not native parametric feature history. ' +
          'Faces, edges, vertices, and solids are reported under Geometry; holes, pockets, ' +
          'fillets, and other design features cannot be identified reliably from this file.'
        : 'This source does not provide normalized feature history.'
      : 'No normalized feature records were extracted from this source.';
  const assemblyRows =
    item.item_type === 'ASSEMBLY'
      ? rows([
          ['Assembly', 'Yes'],
          ['Instances', assembly.instance_count],
          ['Unique components', assembly.unique_component_count],
          ['Subassemblies', assembly.subassembly_count],
        ])
      : [];
  const drawingPageCount = drawings.reduce(
    (total, drawing) => total + (typeof drawing.page_count === 'number' ? drawing.page_count : 0),
    0
  );
  const drawingSummary =
    drawings.length > 0 || drawingSheets.length > 0
      ? [
          {
            label: 'Pages',
            count: drawingPageCount || drawingSheets.length,
            detail: 'sheets' as const,
          },
          { label: 'Sheets', count: drawingSheets.length, detail: 'sheets' as const },
          {
            label: 'Layers',
            count:
              drawingLayers.length ||
              (typeof customProperties.layer_count === 'number' ? customProperties.layer_count : 0),
            detail: 'layers' as const,
          },
          {
            label: 'Dimensions & tolerances',
            count: drawingDimensions.length,
            detail: 'dimensions' as const,
          },
          {
            label: 'Annotations',
            count: drawingAnnotations.length,
            detail: 'annotations' as const,
          },
          {
            label: 'Title blocks',
            count:
              titleBlockSections.length ||
              drawingSheets.filter((sheet) => present(sheet.title_block_name)).length ||
              (Object.keys(nativeTitleBlock).length > 0 ||
              Object.keys(customProperties).some((key) => key.startsWith('title_block_'))
                ? 1
                : 0),
            detail: 'title-blocks' as const,
          },
          { label: 'Notes', count: noteSections.length, detail: 'notes' as const },
        ]
      : [];
  const hasTitleBlock =
    Object.keys(nativeTitleBlock).length > 0 ||
    Object.keys(customProperties).some((key) => key.startsWith('title_block_'));
  const titleBlockRows = hasTitleBlock
    ? rows([
        [
          'Validation',
          typeof customProperties.title_block_validation_status === 'string'
            ? formatPropertyName(customProperties.title_block_validation_status)
            : null,
        ],
        ['Part number', identity.part_number],
        ['Part name', identity.name],
        ['Drawing number', customProperties.title_block_drawing_number],
        ['Revision', identity.revision],
        ['Description', identity.description],
        ['Material', material.normalized_name ?? material.name],
        ['Scale', customProperties.title_block_scale],
        ['Drawn by', customProperties.title_block_drawn_by],
        ['Checked by', customProperties.title_block_checked_by],
        ['Approved by', customProperties.title_block_approved_by],
        ['Date', customProperties.title_block_date],
        ['Sheet number', customProperties.title_block_sheet_number],
        ['Validation issues', customProperties.title_block_validation_issues],
      ])
    : [];
  const rawTitleBlockRows = rows(
    Object.entries(nativeTitleBlock)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => [formatPropertyName(key), value])
  );
  const qualityRows = rows([
    ['Geometry', quality.geometry],
    ['Physical', quality.physical],
    ['Features', quality.features],
    ['Assembly', item.item_type === 'ASSEMBLY' ? quality.assembly : 'not_applicable'],
  ]);
  const engineeringPropertyRows = (label: string, value: unknown): Array<[string, unknown]> => {
    if (!present(value)) return [];
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (
        (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))
      ) {
        try {
          return engineeringPropertyRows(label, JSON.parse(trimmed));
        } catch {
          // Retain non-JSON CAD text that merely resembles structured data.
        }
      }
      return trimmed ? [[formatPropertyName(label), trimmed]] : [];
    }
    if (['number', 'boolean'].includes(typeof value)) {
      return [[formatPropertyName(label), value]];
    }
    if (Array.isArray(value)) {
      if (value.length === 0) return [];
      if (value.every((entry) => ['string', 'number', 'boolean'].includes(typeof entry))) {
        return [[formatPropertyName(label), value.join(', ')]];
      }
      return value.flatMap((entry, index) =>
        engineeringPropertyRows(`${label} ${index + 1}`, entry)
      );
    }
    if (typeof value === 'object' && value !== null) {
      return Object.entries(value as Record<string, unknown>).flatMap(([key, entry]) =>
        engineeringPropertyRows(`${label} ${key}`, entry)
      );
    }
    return [];
  };
  const customRows = Object.entries(customProperties)
    .filter(
      ([key]) =>
        !key.startsWith('title_block_') &&
        !['layers', 'layer_count', 'source_filename'].includes(key)
    )
    .flatMap(([key, value]) => engineeringPropertyRows(key, value));
  const hasUsefulData = [
    identityRows,
    geometryRows,
    physicalRows,
    featureRows,
    featureHistoryRows,
    assemblyRows,
    drawingSummary,
    titleBlockRows,
    rawTitleBlockRows,
    sourceCompatibilityRows,
    qualityRows,
    customRows,
  ].some((group) => group.length > 0);
  const PropertyTable = ({ values }: { values: Array<[string, unknown]> }) => (
    <dl className="curated-property-table">
      {values.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{displayMetadataValue(value)}</dd>
        </div>
      ))}
    </dl>
  );

  useEffect(() => {
    if (!drawingDetail && !featureDetail && !limitationsOpen) return undefined;
    const previousFocus = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('.revision-properties-view [role=dialog]');
    const buttons = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), summary, a[href], input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );
    buttons()[0]?.focus();
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawingDetail(undefined);
        setFeatureDetail(undefined);
        setLimitationsOpen(false);
      } else if (event.key === 'Tab') {
        const controls = buttons();
        if (!controls.length) {
          event.preventDefault();
          return;
        }
        const first = controls[0],
          last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', closeOnEscape);
      previousFocus?.focus();
    };
  }, [drawingDetail, featureDetail, limitationsOpen]);

  const selectedFeatureItems = featureDetail ? (featureHistory.get(featureDetail) ?? []) : [];

  const renderFeatureDetail = () =>
    selectedFeatureItems.length > 0 ? (
      <div className="drawing-entity-list">
        {selectedFeatureItems.map((feature, index) => {
          const properties = record(feature.properties);
          const propertyRows = Object.entries(properties)
            .filter(([key]) => key !== 'sourceType')
            .flatMap(([key, value]) => engineeringPropertyRows(key, value));
          return (
            <article
              key={String(
                feature.persistent_id ?? feature.stable_id ?? `${featureDetail}-${index}`
              )}
              className="drawing-entity-card"
            >
              <h4>{String(feature.name ?? `Feature ${index + 1}`)}</h4>
              <PropertyTable
                values={rows([
                  ['Type', feature.type],
                  ['Persistent ID', feature.persistent_id],
                  ['Source type', properties.sourceType],
                  ...propertyRows,
                ])}
              />
            </article>
          );
        })}
      </div>
    ) : (
      <p className="muted">No feature details are available.</p>
    );

  const drawingDetailTitle =
    drawingDetail === 'sheets'
      ? 'Pages & sheets'
      : drawingDetail === 'layers'
        ? 'Layers'
        : drawingDetail === 'dimensions'
          ? 'Dimensions & tolerances'
          : drawingDetail === 'annotations'
            ? 'Annotations'
            : drawingDetail === 'title-blocks'
              ? 'Title blocks'
              : 'Notes';

  const renderDrawingDetail = () => {
    if (drawingDetail === 'sheets') {
      return drawingSheets.length > 0 ? (
        <div className="drawing-sheet-list">
          {drawingSheets.map((sheet, index) => (
            <article
              key={String(sheet.stable_id ?? `${sheet.name ?? 'sheet'}-${index}`)}
              className="drawing-sheet-card"
            >
              <h4>{String(sheet.name ?? `Sheet ${index + 1}`)}</h4>
              <PropertyTable
                values={rows([
                  ['Page', sheet.page],
                  ['Drawing', sheet.drawing_name],
                  ['Source', sheet.source_path],
                  ['Space', sheet.is_model_space === true ? 'Model' : 'Paper'],
                  [
                    'Size',
                    typeof sheet.width_mm === 'number' && typeof sheet.height_mm === 'number'
                      ? `${number(sheet.width_mm, 'mm')} × ${number(sheet.height_mm, 'mm')}`
                      : null,
                  ],
                  ['Views', sheet.view_count],
                  ['Title block', sheet.title_block_name],
                  ['Rotation', sheet.rotation],
                  ['Plot type', sheet.plot_type],
                  ['Entities', sheet.entity_count],
                ])}
              />
            </article>
          ))}
        </div>
      ) : (
        <p className="muted">No sheet details are available.</p>
      );
    }
    if (drawingDetail === 'dimensions') {
      return drawingDimensions.length > 0 ? (
        <div className="drawing-entity-list">
          {drawingDimensions.map((entity, index) => {
            const entityProperties = record(entity.properties);
            return (
              <article
                key={String(entity.stable_id ?? `dimension-${index}`)}
                className="drawing-entity-card"
              >
                <h4>{formatPropertyName(String(entity.type ?? 'Dimension'))}</h4>
                <PropertyTable
                  values={rows([
                    ['Page', entity.page],
                    [
                      'Value',
                      typeof entity.value === 'number'
                        ? number(entity.value, String(entity.unit ?? 'mm'))
                        : null,
                    ],
                    ['Displayed text', entity.text],
                    ['Space', entityProperties.space],
                    ['Layer', entityProperties.layer],
                    ['AutoCAD type', entityProperties.autocad_type],
                  ])}
                />
              </article>
            );
          })}
        </div>
      ) : (
        <p className="muted">No dimensions or tolerances were extracted.</p>
      );
    }
    if (drawingDetail === 'layers') {
      return drawingLayers.length > 0 ? (
        <div className="drawing-entity-list">
          {drawingLayers.map((layer, index) => {
            const layerName = String(layer.name ?? `Layer ${index + 1}`);
            const entityCount = featureItems.filter(
              (item) =>
                String(record(item.properties).layer ?? '').localeCompare(layerName, undefined, {
                  sensitivity: 'accent',
                }) === 0
            ).length;
            return (
              <article key={`${layerName}-${index}`} className="drawing-entity-card">
                <h4>{layerName}</h4>
                <PropertyTable
                  values={rows([
                    [
                      'State',
                      layer.is_off === true ? 'Off' : layer.is_frozen === true ? 'Frozen' : 'On',
                    ],
                    [
                      'Locked',
                      typeof layer.is_locked === 'boolean'
                        ? layer.is_locked
                          ? 'Yes'
                          : 'No'
                        : null,
                    ],
                    [
                      'Plottable',
                      typeof layer.is_plottable === 'boolean'
                        ? layer.is_plottable
                          ? 'Yes'
                          : 'No'
                        : null,
                    ],
                    ['Color index', layer.color_index],
                    ['Linetype ID', layer.linetype_id],
                    ['Entities', entityCount || null],
                  ])}
                />
              </article>
            );
          })}
        </div>
      ) : (
        <p className="muted">
          The drawing reports layers, but detailed layer records are unavailable.
        </p>
      );
    }
    if (drawingDetail === 'annotations') {
      return drawingAnnotations.length > 0 ? (
        <div className="drawing-entity-list">
          {drawingAnnotations.map((entity, index) => {
            const entityProperties = record(entity.properties);
            return (
              <article
                key={String(entity.stable_id ?? `annotation-${index}`)}
                className="drawing-entity-card"
              >
                <h4>Annotation {index + 1}</h4>
                <PropertyTable
                  values={rows([
                    ...annotationRows(entity.text),
                    ['Page', entity.page],
                    ['Space', entityProperties.space],
                    ['Layer', entityProperties.layer],
                    ['AutoCAD type', entityProperties.autocad_type],
                  ])}
                />
              </article>
            );
          })}
        </div>
      ) : (
        <p className="muted">No annotations were extracted.</p>
      );
    }
    if (drawingDetail === 'title-blocks') {
      return (
        <div className="drawing-detail-groups">
          {titleBlockRows.length > 0 && (
            <section>
              <h4>Normalized fields</h4>
              <PropertyTable values={titleBlockRows} />
            </section>
          )}
          {titleBlockSections.map((section, index) => (
            <section key={String(section.stable_id ?? `title-block-${index}`)}>
              <h4>Title block {index + 1}</h4>
              <PropertyTable
                values={rows([
                  ['Page', section.page],
                  ['Text', section.text],
                ])}
              />
            </section>
          ))}
          {drawingSheets
            .filter((sheet) => present(sheet.title_block_name))
            .map((sheet, index) => (
              <section key={`sheet-title-block-${String(sheet.stable_id ?? index)}`}>
                <h4>{String(sheet.title_block_name)}</h4>
                <PropertyTable
                  values={rows([
                    ['Drawing', sheet.drawing_name],
                    ['Sheet', sheet.name],
                    ['Page', sheet.page],
                  ])}
                />
              </section>
            ))}
          {rawTitleBlockRows.length > 0 && (
            <section>
              <h4>Source fields</h4>
              <p className="muted">Original AutoCAD attribute tags retained for traceability.</p>
              <PropertyTable values={rawTitleBlockRows} />
            </section>
          )}
          {titleBlockRows.length === 0 &&
            titleBlockSections.length === 0 &&
            !drawingSheets.some((sheet) => present(sheet.title_block_name)) &&
            rawTitleBlockRows.length === 0 && (
              <p className="muted">No title-block details were extracted.</p>
            )}
        </div>
      );
    }
    return noteSections.length > 0 ? (
      <div className="drawing-entity-list">
        {noteSections.map((section, index) => (
          <article
            key={String(section.stable_id ?? `note-${index}`)}
            className="drawing-entity-card"
          >
            <h4>Note {index + 1}</h4>
            <PropertyTable
              values={rows([['Page', section.page], ...annotationRows(section.text)])}
            />
          </article>
        ))}
      </div>
    ) : (
      <p className="muted">No drawing notes were extracted.</p>
    );
  };

  return (
    <div className="item-revision-section">
      <div className="item-revision-section-header">
        <div>
          <h2>Properties</h2>

          <p className="muted">CAD and derived metadata associated with this revision.</p>
        </div>
      </div>

      {hasUsefulData ? (
        <div className="curated-properties-grid">
          {identityRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Identity</h3>
              <PropertyTable values={identityRows} />
            </section>
          )}
          {geometryRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Geometry</h3>
              <PropertyTable values={geometryRows} />
            </section>
          )}
          {physicalRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Physical</h3>
              <PropertyTable values={physicalRows} />
            </section>
          )}
          {featureRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Feature summary</h3>
              <PropertyTable values={featureRows} />
            </section>
          )}
          {featureHistoryRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Feature history</h3>
              <dl className="curated-property-table drawing-summary-table">
                {featureHistoryRows.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>
                      <button
                        type="button"
                        className="drawing-detail-link"
                        onClick={() => setFeatureDetail(label)}
                      >
                        {displayMetadataValue(value)}
                      </button>
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {featureRows.length === 0 &&
            featureHistoryRows.length === 0 &&
            featureAvailabilityRows.length > 0 && (
              <section className="item-revision-panel">
                <h3>Features</h3>
                <PropertyTable
                  values={featureAvailabilityRows.map(([label, value]) => [
                    label,
                    typeof value === 'string' ? formatPropertyName(value) : value,
                  ])}
                />
                <p className="muted">{featureUnavailableMessage}</p>
              </section>
            )}
          {assemblyRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Assembly</h3>
              <PropertyTable values={assemblyRows} />
            </section>
          )}
          {drawingSummary.length > 0 && (
            <section className="item-revision-panel">
              <h3>Drawing</h3>
              <dl className="curated-property-table drawing-summary-table">
                {drawingSummary.map(({ label, count, detail }) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>
                      {count > 0 ? (
                        <button
                          type="button"
                          className="drawing-detail-link"
                          onClick={() => setDrawingDetail(detail)}
                        >
                          {count}
                        </button>
                      ) : (
                        '0'
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          {customRows.length > 0 && (
            <section className="item-revision-panel">
              <h3>Engineering properties</h3>
              <PropertyTable values={customRows} />
            </section>
          )}
          {sourceCompatibilityRows.length > 0 && (
            <section
              className={`item-revision-panel source-compatibility-panel${missingWorkbenchModules.length > 0 ? ' has-compatibility-warning' : ''}`}
            >
              <h3>Source &amp; compatibility</h3>
              <PropertyTable values={sourceCompatibilityRows} />
              {missingWorkbenchModules.length > 0 && (
                <p className="compatibility-warning" role="alert">
                  This revision requires FreeCAD workbench modules that are unavailable to the
                  processing worker.
                </p>
              )}
            </section>
          )}
          {(qualityRows.length > 0 || warnings.length > 0) && (
            <section className="item-revision-panel property-quality-panel">
              <h3>Metadata quality</h3>
              <PropertyTable
                values={qualityRows.map(([label, value]) => [
                  label,
                  typeof value === 'string' ? formatPropertyName(value) : value,
                ])}
              />
              {limitations.length > 0 && (
                <div className="property-warnings">
                  <div>
                    <strong>Limitations</strong>
                    <span>
                      {limitations.length}{' '}
                      {limitations.length === 1 ? 'issue affects' : 'issues affect'} extracted
                      metadata.
                    </span>
                  </div>
                  <button
                    type="button"
                    className="property-limitation-link"
                    onClick={() => setLimitationsOpen(true)}
                  >
                    View{' '}
                    {limitations.length === 1
                      ? 'limitation'
                      : `all ${limitations.length} limitations`}
                  </button>
                </div>
              )}
              {present(source.extracted_at) && (
                <p className="muted property-extracted">
                  Extracted {dateTime(String(source.extracted_at))}
                </p>
              )}
            </section>
          )}
        </div>
      ) : (
        <section className="item-revision-panel">
          <div className="empty-state">
            <strong>No properties</strong>

            <p className="muted">No CAD metadata has been generated for this revision.</p>
          </div>
        </section>
      )}
      {drawingDetail && (
        <div
          className="drawing-detail-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setDrawingDetail(undefined);
          }}
        >
          <section
            className="drawing-detail-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="drawing-detail-title"
          >
            <header className="drawing-detail-dialog-header">
              <h3 id="drawing-detail-title">{drawingDetailTitle}</h3>
              <button
                type="button"
                className="drawing-detail-close"
                aria-label="Close drawing details"
                onClick={() => setDrawingDetail(undefined)}
              >
                ×
              </button>
            </header>
            <div className="drawing-detail-dialog-content">{renderDrawingDetail()}</div>
          </section>
        </div>
      )}
      {featureDetail && (
        <div
          className="drawing-detail-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setFeatureDetail(undefined);
          }}
        >
          <section
            className="drawing-detail-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feature-detail-title"
          >
            <header className="drawing-detail-dialog-header">
              <div>
                <h3 id="feature-detail-title">{featureDetail}</h3>
                <span className="muted">
                  {selectedFeatureItems.length}{' '}
                  {selectedFeatureItems.length === 1 ? 'feature' : 'features'}
                </span>
              </div>
              <button
                type="button"
                className="drawing-detail-close"
                aria-label="Close feature details"
                onClick={() => setFeatureDetail(undefined)}
              >
                ×
              </button>
            </header>
            <div className="drawing-detail-dialog-content">{renderFeatureDetail()}</div>
          </section>
        </div>
      )}
      {limitationsOpen && (
        <div
          className="drawing-detail-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setLimitationsOpen(false);
          }}
        >
          <section
            className="drawing-detail-dialog limitation-detail-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="limitation-detail-title"
          >
            <header className="drawing-detail-dialog-header">
              <div>
                <h3 id="limitation-detail-title">Metadata limitations</h3>
                <span className="muted">
                  {limitations.length} {limitations.length === 1 ? 'issue' : 'issues'}
                </span>
              </div>
              <button
                type="button"
                className="drawing-detail-close"
                aria-label="Close limitation details"
                onClick={() => setLimitationsOpen(false)}
              >
                ×
              </button>
            </header>
            <div className="drawing-detail-dialog-content limitation-detail-content">
              <p className="muted limitation-detail-guidance">
                Values in the listed areas may be missing, incomplete, or derived from reduced
                evidence. Other extracted metadata is unaffected.
              </p>
              <ol className="limitation-detail-list">
                {limitations.map((limitation, index) => (
                  <li key={`${limitation.diagnostic}-${index}`}>
                    <article>
                      <h4>{limitation.title}</h4>
                      <strong>Affected properties or parameters</strong>
                      <ul>
                        {limitation.affectedData.map((field) => (
                          <li key={field}>{field}</li>
                        ))}
                      </ul>
                      <details>
                        <summary>Worker diagnostic</summary>
                        <p>{limitation.diagnostic}</p>
                      </details>
                    </article>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

/* ========================================================================== 
   3D Preview
   ========================================================================== */
