import { api } from '../../workflow-api';

export type DuplicateMatch = {
  item_id: string;
  item_number: string;
  item_name: string;
  item_type: string;
  revision_id: string;
  revision_code: string;
  duplicate_check_id?: string;
  duplicate_candidate_id?: string;
  classification?: string;
};
export type IdentityReview = {
  logical_path: string;
  item_number_exists: boolean;
  existing_item_id: string | null;
  duplicates: DuplicateMatch[];
};

export type FileEntry = {
  file: File;
  logicalPath: string;
  externalCadSystem?: string;
  documentType?:
    'PART' | 'ASSEMBLY' | 'DRAWING' | 'PRESENTATION' | 'PROJECT' | 'SUPPORTING' | 'UNKNOWN';
  sha256: string | null;
  status:
    'pending' | 'hashing' | 'checking' | 'duplicate' | 'ready' | 'uploading' | 'complete' | 'error';
  isDuplicate: boolean;
  existingItemNumber?: string;
  existingItemName?: string;
  existingRevisionCode?: string;
  itemNumber: string;
  itemName: string;
  description: string;
  revision: string;
  category: string;
  uploadProgress: number;
  errorMessage?: string;
  formError?: string;
  duplicates?: DuplicateMatch[];
  duplicateChoice?: 'USE_EXISTING' | 'CREATE_NEW';
  selectedRevisionId?: string;
  createdItemId?: string;
};

export async function checkIdentities(entries: FileEntry[]): Promise<IdentityReview[]> {
  const result = await api<{ files: IdentityReview[] }>('/staging/upload-preflight', 'POST', {
    files: entries.map((entry) => ({
      logical_path: entry.logicalPath,
      sha256: entry.sha256,
      byte_size: entry.file.size,
      item_number: entry.itemNumber.trim() || null,
    })),
  });
  return result.files;
}

export type AssemblyReconciliationEntry = {
  parent_logical_path?: string | null;
  depth?: number;
  occurrence_key: string;
  status:
    | 'UNCHANGED'
    | 'MODIFIED'
    | 'ADDED'
    | 'REMOVED'
    | 'MOVED_RENAMED'
    | 'RELINKED'
    | 'MISSING'
    | 'CONFLICT';
  old: Record<string, any> | null;
  new: Record<string, any> | null;
  reason: string;
  duplicate_candidates?: DuplicateMatch[];
};

export type StagingResult = {
  id: string;
  snapshot_version: number;
  status: string;
  operation?: string;
  base_revision_id?: string | null;
  base_snapshot_id?: string | null;
  proposal?: any;
  result?: {
    assembly_analysis?: {
      snapshot_version: number;
      report: CadDependencyReport;
      files: Array<{ logical_path: string; filename: string; byte_size: number; sha256: string }>;
      root_file_reconciliation?: AssemblyReconciliationEntry;
      reconciliation: AssemblyReconciliationEntry[];
      safe_changes_applied?: boolean;
      package_staged?: boolean;
      identity_decisions?: Record<
        string,
        {
          decision: 'USE_EXISTING' | 'CREATE_NEW';
          existing_revision_id?: string | null;
          item?: {
            item_number: string;
            name: string;
            item_type: string;
            description?: string | null;
            category?: string | null;
          } | null;
          revision?: {
            revision_code: string;
            description?: string | null;
            cad_system: string;
            native_format: string;
          } | null;
        }
      >;
    };
  };
  files: Array<{
    id: string;
    logical_path: string;
    filename: string;
    byte_size: number;
    sha256: string;
    mime_type: string;
    state: string;
    upload_url: string;
  }>;
  retained_artifacts: Array<{
    relation_id: string | null;
    artifact_id: string;
    files: Array<{
      id: string;
      reference_name: string | null;
      filename: string;
      byte_size: number;
      sha256: string;
      mime_type: string;
      download_url: string;
    }>;
  }>;
  duplicate_check: {
    id: string;
    candidates: Array<{
      id: string;
      candidate_revision_id: string;
      candidate_item_id: string;
      item_number: string;
      revision: string;
      classification: string;
      reasons: string[];
    }>;
  };
};

export type StagingCommitResult = {
  staging_id: string;
  commit_id: string;
  snapshot_version: number;
  item_id: string;
  revision_id: string;
  content_snapshot_id: string;
  artifact_ids: string[];
};
export type StagingWorkflowResult = {
  staging_id: string;
  state: string;
  stage: string;
  progress_percent: number;
  error_detail: string | null;
};
export type CommitProcessingJob = {
  id: string;
  type: string;
  state: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  stage: string;
  progress_percent: number;
  error_detail: string | null;
  attempt_count: number;
};
export type EditWhereUsed = {
  items: Array<{
    occurrence_key: string;
    direct: boolean;
    depth: number;
    total_quantity: number;
    parent_part: { id: string; part_number: string; name: string };
    parent_revision: { id: string; revision_code: string };
  }>;
  warnings: string[];
};
export type WorkerPreflight = {
  worker_type: 'FREECAD' | 'AUTOCAD' | 'INVENTOR' | 'STEP';
  available: boolean;
  compatible: boolean;
  status: 'IDLE' | 'BUSY' | 'DEGRADED' | null;
  message: string;
};

export type GeneratedItemNumber = {
  item_number: string;
  item_type: 'PART' | 'ASSEMBLY';
  template: string;
};

export type ItemNumberTemplateOption = {
  id: string;
  name: string;
  is_selected: boolean;
  part_example: string;
  assembly_example: string;
};

export type CadDependencyReport = {
  package_id?: string;
  package_url?: string;
  contract_version?: string;
  analyzer_version?: string;
  part_metadata?: Record<string, unknown>;
  metadata_source_sha256?: string;
  document_type: 'PART' | 'ASSEMBLY' | 'DRAWING';
  root_file: string;
  root_logical_path: string;
  files: Array<{
    logical_path: string;
    is_root: boolean;
    document_type?:
      'PART' | 'ASSEMBLY' | 'DRAWING' | 'PRESENTATION' | 'PROJECT' | 'SUPPORTING' | 'UNKNOWN';
    assembly_model?:
      'NONE' | 'APP_PART' | 'APP_LINK' | 'BUILTIN_ASSEMBLY' | 'ASSEMBLY3' | 'ASSEMBLY4';
  }>;
  references?: Array<{
    parent_logical_path: string;
    child_logical_path: string;
    instance_name?: string;
    occurrence_id?: string;
    occurrence_path?: string;
    occurrence_key?: string;
    transform?: number[];
    is_visible?: boolean;
    is_suppressed?: boolean;
    source_reference?: string;
    reference_designator?: string;
    properties?: Record<string, unknown>;
  }>;
  isolated_reopen_validated?: boolean;
  missing_references: Array<{
    reference: string | null;
    referenced_by: string;
    kind?: string;
    assembly_model?: string;
    object?: string;
    property?: string;
    suggestion?: string;
  }>;
  missing_supporting_references?: Array<{
    reference: string | null;
    referenced_by: string;
    kind?: string;
  }>;
  validation_issues?: Array<{
    code: string;
    severity: 'ERROR' | 'WARNING';
    document: string;
    assembly_model?: string;
    object?: string;
    property?: string;
    property_type?: string;
    cycle?: string[];
    message: string;
    suggestion: string;
  }>;
};

// NX can report the same display name for repeated occurrences of one prototype.
// Allocate unique proposal keys within each parent without treating these keys as native IDs.
export const nxBomInstanceNames = (
  references: NonNullable<CadDependencyReport['references']>
): string[] => {
  const names = references.map((reference) =>
    (
      reference.occurrence_key ||
      reference.occurrence_path ||
      `${reference.parent_logical_path}::${
        reference.occurrence_id || reference.instance_name || reference.child_logical_path
      }`
    ).slice(0, 255)
  );
  const reserved = new Map<string, Set<string>>();
  references.forEach((reference, index) => {
    const parent = reference.parent_logical_path.toLocaleLowerCase();
    const used = reserved.get(parent) ?? new Set<string>();
    used.add(names[index].toLocaleLowerCase());
    reserved.set(parent, used);
  });
  const seen = new Map<string, Set<string>>();
  return references.map((reference, index) => {
    const parent = reference.parent_logical_path.toLocaleLowerCase();
    const used = reserved.get(parent)!;
    const encountered = seen.get(parent) ?? new Set<string>();
    seen.set(parent, encountered);
    const original = names[index];
    if (!encountered.has(original.toLocaleLowerCase())) {
      encountered.add(original.toLocaleLowerCase());
      used.add(original.toLocaleLowerCase());
      return original;
    }
    let sequence = 2;
    let candidate: string;
    do {
      const suffix = ` [${sequence++}]`;
      candidate = `${original.slice(0, 255 - suffix.length)}${suffix}`;
    } while (used.has(candidate.toLocaleLowerCase()));
    used.add(candidate.toLocaleLowerCase());
    encountered.add(candidate.toLocaleLowerCase());
    return candidate;
  });
};

export type CadBundleManifest = {
  schemaVersion: number;
  revisionId: string;
  snapshotId: string;
  root?: { path: string; itemId?: string; partNumber?: string; revisionId?: string };
  files: Array<{
    path: string;
    itemId?: string;
    partNumber?: string;
    revisionId: string;
    role?: string;
    byteSize: number;
    sha256: string;
  }>;
};

export const cadProfile = (filename: string, documentType?: FileEntry['documentType']) => {
  const extension = filename.split('.').pop()?.toLowerCase();
  if (extension === 'prt') {
    const assembly = documentType === 'ASSEMBLY';
    return {
      cadSystem: 'Siemens NX',
      nativeFormat: 'PRT',
      artifactType: assembly ? 'CAD_ASSEMBLY' : 'CAD_MODEL',
      itemType: assembly ? 'ASSEMBLY' : 'PART',
    };
  }
  if (extension === 'dwg' || extension === 'dxf') {
    return {
      cadSystem: 'AutoCAD',
      nativeFormat: extension.toUpperCase(),
      artifactType: 'CAD_MODEL',
      itemType: 'PART',
    };
  }
  if (['ipt', 'iam', 'idw'].includes(extension ?? '')) {
    return {
      cadSystem: 'Inventor',
      nativeFormat: extension!.toUpperCase(),
      artifactType:
        extension === 'iam' ? 'CAD_ASSEMBLY' : extension === 'idw' ? 'DRAWING' : 'CAD_MODEL',
      itemType: extension === 'iam' ? 'ASSEMBLY' : 'PART',
    };
  }
  if (extension === 'step' || extension === 'stp') {
    return { cadSystem: 'STEP', nativeFormat: 'STEP', artifactType: 'STEP', itemType: 'PART' };
  }
  return {
    cadSystem: extension === 'fcstd' ? 'FreeCAD' : 'Unknown',
    nativeFormat: extension === 'fcstd' ? 'FCStd' : (extension ?? 'unknown').toUpperCase(),
    artifactType:
      documentType === 'ASSEMBLY'
        ? 'CAD_ASSEMBLY'
        : documentType === 'DRAWING'
          ? 'DRAWING'
          : 'CAD_MODEL',
    itemType: documentType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
  };
};

export const logicalPathDepth = (path: string) => path.split(/[\\/]/).length - 1;

export const isManagedCadDefinition = (entry: FileEntry) =>
  /\.(iam|ipt)$/i.test(entry.file.name) ||
  entry.documentType === 'ASSEMBLY' ||
  entry.documentType === 'PART';

export const createsManagedItem = (
  entry: FileEntry,
  report: CadDependencyReport | null | undefined
) =>
  entry.externalCadSystem && entry.documentType === 'SUPPORTING'
    ? false
    : isManagedCadDefinition(entry) || !report || entry.logicalPath === report.root_logical_path;

export const isProcessingSupport = (path: string, documentType?: string) =>
  /\.ipj$/i.test(path) || documentType === 'PROJECT' || documentType === 'SUPPORTING';

export const isDrawing = (entry: FileEntry) =>
  /\.(idw|dwg|dxf)$/i.test(entry.file.name) || entry.documentType === 'DRAWING';

export const relatedSupportPaths = (
  ownerPath: string,
  supportPaths: string[],
  references: NonNullable<CadDependencyReport['references']>
) => {
  const candidates = new Set(supportPaths);
  return [
    ...new Set(
      references.flatMap((reference) => {
        if (
          reference.child_logical_path === ownerPath &&
          candidates.has(reference.parent_logical_path)
        )
          return [reference.parent_logical_path];
        if (
          reference.parent_logical_path === ownerPath &&
          candidates.has(reference.child_logical_path)
        )
          return [reference.child_logical_path];
        return [];
      })
    ),
  ];
};

export const withoutProcessingSupport = (report: CadDependencyReport): CadDependencyReport => {
  const excluded = new Set(
    report.files
      .filter((file) => isProcessingSupport(file.logical_path, file.document_type))
      .map((file) => file.logical_path)
  );
  const missingSupporting = report.missing_references.filter((entry) => {
    if (entry.kind?.toLocaleLowerCase() === 'linked-resource') return true;
    const extension = entry.reference
      ?.replace(/\\/g, '/')
      .split('/')
      .pop()
      ?.match(/\.[^.]+$/)?.[0]
      ?.toLocaleLowerCase();
    return Boolean(
      extension &&
      !['.prt', '.iam', '.ipt', '.idw', '.ipn', '.dwg', '.dxf', '.fcstd'].includes(extension)
    );
  });
  const missingCad = report.missing_references.filter(
    (entry) => !missingSupporting.includes(entry)
  );
  if (excluded.size === 0 && missingSupporting.length === 0) return report;
  return {
    ...report,
    files: report.files.filter((file) => !excluded.has(file.logical_path)),
    references: report.references?.filter(
      (reference) =>
        !excluded.has(reference.parent_logical_path) && !excluded.has(reference.child_logical_path)
    ),
    missing_references: missingCad,
    missing_supporting_references: [
      ...(report.missing_supporting_references ?? []),
      ...missingSupporting,
    ],
  };
};

export const inventorBomInstanceNames = (entries: FileEntry[]): Map<string, string> => {
  const normalized = entries.map((entry) => ({
    entry,
    base: entry.logicalPath.replace(/\\/g, '/').replace(/\.(ipt|iam)$/i, ''),
    kind: /\.iam$/i.test(entry.logicalPath) ? 'Assembly' : 'Part',
  }));
  const counts = new Map<string, number>();
  normalized.forEach(({ base }) =>
    counts.set(base.toLocaleLowerCase(), (counts.get(base.toLocaleLowerCase()) ?? 0) + 1)
  );
  const used = new Set<string>();
  return new Map(
    normalized.map(({ entry, base, kind }) => {
      const needsKind = (counts.get(base.toLocaleLowerCase()) ?? 0) > 1;
      const initial = needsKind ? `${base} [${kind}]` : base;
      let candidate = initial;
      let sequence = 2;
      while (used.has(candidate.toLocaleLowerCase())) candidate = `${initial} ${sequence++}`;
      used.add(candidate.toLocaleLowerCase());
      return [entry.logicalPath, candidate];
    })
  );
};

export type EditOccurrence = {
  id: string;
  occurrence_key: string;
  source_reference: string;
  quantity: number;
  child_item: { id: string; item_number: string; name: string; item_type: string };
  child_revision: {
    id: string;
    revision_code: string;
    lifecycle_state: string;
    checkout_status: string;
  };
  children: EditOccurrence[];
};

export const flattenEditOccurrences = (
  occurrences: EditOccurrence[],
  depth = 0
): Array<{ occurrence: EditOccurrence; depth: number }> =>
  occurrences.flatMap((occurrence) => [
    { occurrence, depth },
    ...flattenEditOccurrences(occurrence.children ?? [], depth + 1),
  ]);

export const normalizedCadReference = (value: string | null | undefined) =>
  (value ?? '').replace(/\\/g, '/').replace(/^\.\//, '').toLocaleLowerCase();

export const retainedFileOccurrence = (
  file: { reference_name: string | null; filename: string },
  occurrences: EditOccurrence[]
): EditOccurrence | undefined => {
  if (!/\.(?:iam|ipt)$/i.test(file.filename)) return undefined;
  const flattened = flattenEditOccurrences(occurrences).map(({ occurrence }) => occurrence);
  const reference = normalizedCadReference(file.reference_name);
  const filename = normalizedCadReference(file.filename);
  const fileBasename = filename.split('/').at(-1) ?? '';
  const stemIdentity = fileBasename.replace(/\.[^.]+$/, '').replace(/[^a-z0-9]+/g, '');
  const expectedType = /\.iam$/i.test(file.filename) ? 'ASSEMBLY' : 'PART';
  return flattened
    .map((occurrence) => {
      const source = normalizedCadReference(occurrence.source_reference);
      const sourceBasename = source.split('/').at(-1) ?? '';
      const nameIdentity = occurrence.child_item.name
        .toLocaleLowerCase()
        .replace(/[^a-z0-9]+/g, '');
      const numberIdentity = occurrence.child_item.item_number
        .toLocaleLowerCase()
        .replace(/[^a-z0-9]+/g, '');
      let score = 0;
      if (reference && reference === source) score = 100;
      else if (reference && (reference.endsWith(`/${source}`) || source.endsWith(`/${reference}`)))
        score = 95;
      else if (sourceBasename === fileBasename) score = 90;
      else if (stemIdentity === nameIdentity) score = 80;
      else if (stemIdentity === numberIdentity) score = 75;
      if (occurrence.child_item.item_type !== expectedType) score = 0;
      return { occurrence, score };
    })
    .filter((candidate) => candidate.score > 0)
    .sort((left, right) => right.score - left.score)[0]?.occurrence;
};

export const setOccurrenceCheckoutStatus = (
  occurrences: EditOccurrence[],
  revisionId: string,
  checkoutStatus: string
): EditOccurrence[] =>
  occurrences.map((occurrence) => ({
    ...occurrence,
    child_revision:
      occurrence.child_revision.id === revisionId
        ? { ...occurrence.child_revision, checkout_status: checkoutStatus }
        : occurrence.child_revision,
    children: setOccurrenceCheckoutStatus(occurrence.children ?? [], revisionId, checkoutStatus),
  }));

class IncrementalSha256 {
  private readonly state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  private readonly block = new Uint8Array(64);
  private blockLength = 0;
  private byteLength = 0;

  update(input: Uint8Array): void {
    this.byteLength += input.length;
    let offset = 0;
    if (this.blockLength > 0) {
      const copied = Math.min(64 - this.blockLength, input.length);
      this.block.set(input.subarray(0, copied), this.blockLength);
      this.blockLength += copied;
      offset = copied;
      if (this.blockLength === 64) {
        this.processBlock(this.block);
        this.blockLength = 0;
      }
    }
    while (offset + 64 <= input.length) {
      this.processBlock(input.subarray(offset, offset + 64));
      offset += 64;
    }
    if (offset < input.length) {
      this.block.set(input.subarray(offset), 0);
      this.blockLength = input.length - offset;
    }
  }

  digest(): string {
    const bitLength = this.byteLength * 8;
    this.block[this.blockLength++] = 0x80;
    if (this.blockLength > 56) {
      this.block.fill(0, this.blockLength);
      this.processBlock(this.block);
      this.blockLength = 0;
    }
    this.block.fill(0, this.blockLength, 56);
    const length = new DataView(this.block.buffer);
    length.setUint32(56, Math.floor(bitLength / 0x1_0000_0000), false);
    length.setUint32(60, bitLength >>> 0, false);
    this.processBlock(this.block);
    return Array.from(this.state, (word) => word.toString(16).padStart(8, '0')).join('');
  }

  private processBlock(input: Uint8Array): void {
    const words = new Uint32Array(64);
    for (let index = 0; index < 16; index++) {
      const offset = index * 4;
      words[index] =
        ((input[offset] << 24) |
          (input[offset + 1] << 16) |
          (input[offset + 2] << 8) |
          input[offset + 3]) >>>
        0;
    }
    for (let index = 16; index < 64; index++) {
      const x = words[index - 15];
      const y = words[index - 2];
      const small0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
      const small1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      words[index] = (words[index - 16] + small0 + words[index - 7] + small1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = this.state;
    for (let index = 0; index < 64; index++) {
      const sigma1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const choice = (e & f) ^ (~e & g);
      const temporary1 = (h + sigma1 + choice + SHA256_CONSTANTS[index] + words[index]) >>> 0;
      const sigma0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temporary2 = (sigma0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temporary1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temporary1 + temporary2) >>> 0;
    }
    this.state[0] = (this.state[0] + a) >>> 0;
    this.state[1] = (this.state[1] + b) >>> 0;
    this.state[2] = (this.state[2] + c) >>> 0;
    this.state[3] = (this.state[3] + d) >>> 0;
    this.state[4] = (this.state[4] + e) >>> 0;
    this.state[5] = (this.state[5] + f) >>> 0;
    this.state[6] = (this.state[6] + g) >>> 0;
    this.state[7] = (this.state[7] + h) >>> 0;
  }
}

const SHA256_CONSTANTS = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export async function fileSha256(
  file: File,
  onProgress?: (percent: number) => void
): Promise<string> {
  const chunkSize = 4 * 1024 * 1024;
  const chunks = Math.max(1, Math.ceil(file.size / chunkSize));
  const hash = new IncrementalSha256();
  for (let index = 0; index < chunks; index++) {
    const offset = index * chunkSize;
    hash.update(new Uint8Array(await file.slice(offset, offset + chunkSize).arrayBuffer()));
    onProgress?.(Math.round(((index + 1) / chunks) * 100));
  }
  return hash.digest();
}
