import type { Dispatch, SetStateAction } from 'react';
import { api, authorizationHeader, serverApiUrl } from '../../workflow-api';
import { randomUUID } from '../../uuid';
import {
  type CadDependencyReport,
  type FileEntry,
  type StagingResult,
  type StagingWorkflowResult,
  cadProfile,
  createsManagedItem,
  bomInstanceNames,
  isDrawing,
  relatedSupportPaths,
} from './model';

type Context = {
  files: FileEntry[];
  mainFileName: string;
  updateEntry: (index: number, updates: Partial<FileEntry>) => void;
  reviewIdentities: (entries: FileEntry[]) => Promise<boolean>;
  stagingId: string | null;
  setFiles: Dispatch<SetStateAction<FileEntry[]>>;
  setCommittedItemId: Dispatch<SetStateAction<string | null>>;
  setCommitMessage: Dispatch<SetStateAction<string>>;
  targetItemNumber: string;
  targetItemName: string;
  dependencyReport: CadDependencyReport | null;
  setStagingSnapshotVersion: Dispatch<SetStateAction<number | null>>;
  targetItemId: string | null;
  targetRevisionId: string | null;
};

export function createAssemblyUploadActions(context: Context) {
  const {
    files,
    mainFileName,
    updateEntry,
    reviewIdentities,
    stagingId,
    setFiles,
    setCommittedItemId,
    setCommitMessage,
    targetItemNumber,
    targetItemName,
    dependencyReport,
    setStagingSnapshotVersion,
  } = context;
  const uploadAssembly = async () => {
    const packageEntries = files.filter((entry) => entry.status === 'ready');
    const ready = packageEntries.filter((entry) => createsManagedItem(entry, dependencyReport));
    if (ready.length === 0) return;
    if (!mainFileName || !ready.some((entry) => entry.logicalPath === mainFileName)) return;
    const main = ready.find((entry) => entry.logicalPath === mainFileName)!;
    const mainProfile = cadProfile(main.file.name, main.documentType);
    if (main.externalCadSystem) mainProfile.cadSystem = main.externalCadSystem;
    let hasMissingIdentity = false;
    for (const entry of ready) {
      if (entry.duplicateChoice === 'USE_EXISTING') continue;
      const missingIdentity = [
        !entry.itemNumber.trim()
          ? `${entry === main ? 'Assembly' : 'Component'} item number is not specified.`
          : '',
        !entry.itemName.trim() ? 'Item name is not specified.' : '',
        !entry.revision.trim() ? 'Revision is not specified.' : '',
      ].filter(Boolean);
      if (missingIdentity.length) {
        updateEntry(files.indexOf(entry), { formError: missingIdentity.join(' ') });
        hasMissingIdentity = true;
      }
    }
    if (hasMissingIdentity) return;
    if (!(await reviewIdentities(ready))) return;
    const mainIndex = files.indexOf(main);
    if (main.duplicateChoice === 'USE_EXISTING') {
      const selected = main.duplicates?.find(
        (candidate) => candidate.revision_id === main.selectedRevisionId
      );
      if (!selected) {
        updateEntry(mainIndex, { formError: 'Select an existing assembly Item.' });
        return;
      }
      if (selected.duplicate_check_id) {
        await api(`/staging/${stagingId}/reuse`, 'POST');
      }
      setFiles((previous) =>
        previous.map((entry) =>
          ready.includes(entry)
            ? {
                ...entry,
                status: 'complete',
                uploadProgress: 100,
                isDuplicate: entry === main,
                createdItemId: entry === main ? selected.item_id : undefined,
              }
            : entry
        )
      );
      setCommittedItemId(selected.item_id);
      setCommitMessage(
        `Using existing assembly ${selected.item_number}. No CAD files were uploaded.`
      );
      return;
    }
    const missing = [
      !(targetItemNumber || main.itemNumber).trim() ? 'Assembly item number is not specified.' : '',
      !(targetItemName || main.itemName).trim() ? 'Assembly item name is not specified.' : '',
      !main.revision.trim() ? 'Revision is not specified.' : '',
    ].filter(Boolean);
    if (missing.length) {
      updateEntry(mainIndex, { formError: missing.join(' ') });
      return;
    }
    if (!stagingId) {
      updateEntry(mainIndex, {
        formError: 'The staging session is unavailable. Choose the folder again.',
      });
      return;
    }
    const readyNames = new Set(packageEntries.map((entry) => entry.logicalPath));
    setFiles((previous) =>
      previous.map((entry) =>
        readyNames.has(entry.logicalPath)
          ? { ...entry, status: 'uploading', uploadProgress: 0, formError: undefined }
          : entry
      )
    );
    try {
      let staging = await api<StagingResult>(`/staging/${stagingId}`);
      const stagedFile = (entry: FileEntry) => {
        const row = staging.files.find((candidate) => candidate.logical_path === entry.logicalPath);
        if (!row) throw new Error(`${entry.file.name} is missing from staging.`);
        return row;
      };
      const createNewEntries = ready.filter((entry) => entry.duplicateChoice !== 'USE_EXISTING');
      const relatedDrawings = packageEntries.filter((entry) => entry !== main && isDrawing(entry));
      const instanceNames = bomInstanceNames(dependencyReport?.references ?? []);
      const packageEntriesFor = (owner: FileEntry) => {
        if (owner.documentType !== 'ASSEMBLY' || !dependencyReport?.references?.length) {
          return [owner];
        }
        const paths = new Set([owner.logicalPath]);
        const pending = [owner.logicalPath];
        while (pending.length) {
          const parent = pending.pop()!;
          for (const reference of dependencyReport.references) {
            if (reference.parent_logical_path !== parent || paths.has(reference.child_logical_path))
              continue;
            paths.add(reference.child_logical_path);
            pending.push(reference.child_logical_path);
          }
        }
        return packageEntries.filter(
          (entry) => paths.has(entry.logicalPath) && !relatedDrawings.includes(entry)
        );
      };
      const artifacts = [
        {
          client_artifact_id: randomUUID(),
          owner_staged_file_id: null,
          artifact_type: 'CAD_ASSEMBLY',
          name: main.itemName.trim(),
          classification: 'AUTHORITATIVE',
          relation_type: 'SPECIFICATION',
          is_primary: true,
          files: packageEntries
            .filter((entry) => !relatedDrawings.includes(entry))
            .map((entry) => ({
              staged_file_id: stagedFile(entry).id,
              reference_name: entry.logicalPath,
              role: entry === main ? 'PRIMARY' : 'SECONDARY',
            })),
        },
        ...relatedDrawings.map((drawing) => ({
          client_artifact_id: randomUUID(),
          owner_staged_file_id: (() => {
            const owner = createNewEntries.find(
              (entry) =>
                entry !== main &&
                relatedSupportPaths(
                  entry.logicalPath,
                  [drawing.logicalPath],
                  dependencyReport?.references ?? []
                ).length > 0
            );
            return owner ? stagedFile(owner).id : null;
          })(),
          artifact_type: 'DRAWING',
          name: drawing.file.name.replace(/\.[^.]+$/, ''),
          classification: 'AUTHORITATIVE',
          relation_type: 'DRAWING',
          is_primary: false,
          files: [
            {
              staged_file_id: stagedFile(drawing).id,
              reference_name: drawing.logicalPath,
              role: 'PRIMARY',
            },
          ],
        })),
        ...createNewEntries
          .filter((entry) => entry !== main)
          .map((entry) => ({
            client_artifact_id: randomUUID(),
            owner_staged_file_id: stagedFile(entry).id,
            artifact_type: entry.documentType === 'ASSEMBLY' ? 'CAD_ASSEMBLY' : 'CAD_MODEL',
            name: entry.itemName.trim(),
            classification: 'AUTHORITATIVE',
            relation_type: 'SPECIFICATION',
            is_primary: true,
            files: packageEntriesFor(entry).map((packageEntry) => ({
              staged_file_id: stagedFile(packageEntry).id,
              reference_name:
                entry.documentType === 'ASSEMBLY' ? packageEntry.logicalPath : 'MODEL',
              role: packageEntry === entry ? 'PRIMARY' : 'SECONDARY',
            })),
          })),
      ];
      const identityDecisions = ready.map((entry) =>
        entry === main
          ? {
              staged_file_id: stagedFile(entry).id,
              decision: 'CREATE_NEW',
            }
          : entry.duplicateChoice === 'USE_EXISTING'
            ? {
                staged_file_id: stagedFile(entry).id,
                decision: 'USE_EXISTING',
                existing_revision_id: entry.selectedRevisionId,
              }
            : {
                staged_file_id: stagedFile(entry).id,
                decision: 'CREATE_NEW',
                item: {
                  item_number: entry.itemNumber.trim(),
                  name: entry.itemName.trim(),
                  item_type: entry.documentType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
                  description: entry.description.trim() || null,
                  category: entry.category.trim() || null,
                },
                revision: {
                  revision_code: entry.revision.trim(),
                  cad_system:
                    entry.externalCadSystem ||
                    cadProfile(entry.file.name, entry.documentType).cadSystem,
                  native_format: cadProfile(entry.file.name, entry.documentType).nativeFormat,
                },
              }
      );
      staging = await api<StagingResult>(`/staging/${stagingId}/proposal`, 'PUT', {
        expected_snapshot_version: staging.snapshot_version,
        operation: 'CREATE_ITEM',
        item: {
          item_number: (targetItemNumber || main.itemNumber).trim(),
          name: (targetItemName || main.itemName).trim(),
          item_type: 'ASSEMBLY',
          description: main.description.trim() || null,
          category: main.category.trim() || null,
        },
        revision: {
          revision_code: main.revision.trim(),
          cad_system: mainProfile.cadSystem,
          native_format: mainProfile.nativeFormat,
        },
        artifacts,
        identity_decisions: identityDecisions,
        bom: dependencyReport?.references?.length
          ? dependencyReport.references
              .filter(
                (reference) =>
                  ready.some((entry) => entry.logicalPath === reference.parent_logical_path) &&
                  ready.some((entry) => entry.logicalPath === reference.child_logical_path)
              )
              .map((reference, index) => {
                const parent = ready.find(
                  (entry) => entry.logicalPath === reference.parent_logical_path
                );
                const child = ready.find(
                  (entry) => entry.logicalPath === reference.child_logical_path
                );
                if (!parent || !child) {
                  throw new Error(
                    `The dependency graph references an unavailable file at relationship ${index + 1}.`
                  );
                }
                return {
                  parent_staged_file_id: stagedFile(parent).id,
                  child_staged_file_id: stagedFile(child).id,
                  instance_name:
                    instanceNames[index] ||
                    reference.occurrence_key ||
                    reference.occurrence_path ||
                    `${reference.parent_logical_path}::${
                      reference.occurrence_id ||
                      reference.instance_name ||
                      reference.child_logical_path
                    }`,
                  quantity: 1,
                  source_reference: reference.source_reference || reference.child_logical_path,
                  reference_designator: reference.reference_designator || reference.instance_name,
                  transform: reference.transform,
                  is_suppressed: reference.is_suppressed ?? false,
                  is_visible: reference.is_visible ?? true,
                  properties: reference.properties || {},
                };
              })
          : ready
              .filter((entry) => entry !== main)
              .map((entry, index) => ({
                parent_staged_file_id: stagedFile(main).id,
                child_staged_file_id: stagedFile(entry).id,
                instance_name: entry.logicalPath || `component-${index + 1}`,
                quantity: 1,
              })),
      });
      setStagingSnapshotVersion(staging.snapshot_version);
      setFiles((previous) =>
        previous.map((entry) =>
          readyNames.has(entry.logicalPath) ? { ...entry, uploadProgress: 10 } : entry
        )
      );

      for (const entry of packageEntries) {
        if (!main.externalCadSystem && entry.duplicateChoice === 'USE_EXISTING') continue;
        const current = staging.files.find((row) => row.logical_path === entry.logicalPath);
        if (!current) throw new Error(`${entry.file.name} is missing from staging.`);
        if (current.state !== 'VERIFIED') {
          const response = await fetch(serverApiUrl(current.upload_url), {
            method: 'PUT',
            headers: { ...authorizationHeader(), 'Content-Type': 'application/octet-stream' },
            body: entry.file,
          });
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(
              typeof body.detail === 'string'
                ? `${entry.file.name}: ${body.detail}`
                : `Upload failed for ${entry.file.name} (${response.status}).`
            );
          }
          staging = (await response.json()) as StagingResult;
        }
        updateEntry(files.indexOf(entry), { uploadProgress: 60 });
      }
      const workflow = await api<StagingWorkflowResult>(`/staging/${stagingId}/submit`, 'POST');
      setCommitMessage(
        'Assembly upload complete. Validation and publication are running in the background.'
      );
      setFiles((previous) =>
        previous.map((entry) =>
          readyNames.has(entry.logicalPath)
            ? { ...entry, status: 'complete', uploadProgress: workflow.progress_percent }
            : entry
        )
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to commit the assembly.';
      setFiles((previous) =>
        previous.map((entry) =>
          readyNames.has(entry.logicalPath)
            ? {
                ...entry,
                status: 'ready',
                uploadProgress: 0,
                formError: entry.logicalPath === mainFileName ? message : undefined,
              }
            : entry
        )
      );
    }
  };

  return { uploadAssembly };
}
