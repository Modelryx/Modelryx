import type { Dispatch, SetStateAction } from "react";
import { api, authorizationHeader, serverApiUrl } from "../../workflow-api";
import { randomUUID } from "../../uuid";
import {
  type AssemblyReconciliationEntry, type CadDependencyReport, type EditOccurrence,
  type StagingResult, fileSha256, flattenEditOccurrences,
  setOccurrenceCheckoutStatus,
} from "./model";

type AssemblyAnalysis = NonNullable<NonNullable<StagingResult["result"]>["assembly_analysis"]>;
type Context = {
  editStaging: StagingResult | null;
  modifiedEditReferences: AssemblyReconciliationEntry[];
  editAssemblyAnalysis: AssemblyAnalysis | undefined;
  editAnalysisIsCurrent: boolean;
  setEditStagingError: Dispatch<SetStateAction<string>>;
  setEditBusy: Dispatch<SetStateAction<boolean>>;
  editOccurrences: EditOccurrence[];
  workerAnalysisFiles: File[];
  childEditSessions: Record<string, StagingResult>;
  editReconciliation: AssemblyReconciliationEntry[];
  setChildEditSessions: Dispatch<SetStateAction<Record<string, StagingResult>>>;
  setEditOccurrences: Dispatch<SetStateAction<EditOccurrence[]>>;
  stageCadFile: (original: StagingResult, file: File, itemType: string) => Promise<StagingResult>;
  dependencyReport: CadDependencyReport | null;
  setEditMessage: Dispatch<SetStateAction<string>>;
};

export function createPrepareModifiedEditChildren(context: Context) {
  const {
    editStaging, modifiedEditReferences, editAssemblyAnalysis, editAnalysisIsCurrent,
    setEditStagingError, setEditBusy, editOccurrences, workerAnalysisFiles,
    childEditSessions, editReconciliation, setChildEditSessions,
    setEditOccurrences, stageCadFile, dependencyReport, setEditMessage,
  } = context;
  const prepareModifiedEditChildren = async () => {
    if (!editStaging || modifiedEditReferences.length === 0) return;
    if (!editAssemblyAnalysis?.package_staged || !editAnalysisIsCurrent) {
      setEditStagingError("Stage the resolved root assembly package before preparing child assemblies.");
      return;
    }
    setEditBusy(true);
    setEditStagingError("");
    try {
      const occurrences = flattenEditOccurrences(editOccurrences).map(({ occurrence }) => occurrence);
      const sources = new Map(workerAnalysisFiles.map((file) => [
        (file.webkitRelativePath || file.name).replace(/\\/g, "/").toLowerCase(), file,
      ]));
      const prepared = new Set<string>();
      for (const entry of modifiedEditReferences) {
        const revisionId = String(entry.old?.child_revision_id ?? "");
        if (!revisionId || prepared.has(revisionId) || childEditSessions[revisionId]) continue;
        const occurrence = occurrences.find((candidate) => candidate.child_revision.id === revisionId);
        if (!occurrence) {
          throw new Error(`The modified occurrence ${entry.occurrence_key} is not in the loaded assembly tree.`);
        }
        const childPath = String(entry.new?.child_logical_path ?? "");
        const source = sources.get(childPath.toLowerCase());
        const child = await api<StagingResult>(
          `/staging/items/${occurrence.child_item.id}/revisions/${revisionId}/edit`,
          "POST",
          { client_staging_id: randomUUID() },
        );
        const nestedChanges = editReconciliation.filter((candidate) =>
          String(candidate.parent_logical_path ?? "").toLowerCase() === childPath.toLowerCase());
        if (!source) {
          if (child.files.length > 0 && nestedChanges.length === 0) {
            const recovered = occurrence.child_item.item_type === "ASSEMBLY"
              ? await api<StagingResult>(
                `/staging/${child.id}/assembly-reconciliation/adopt`,
                "POST",
                {
                  expected_snapshot_version: child.snapshot_version,
                  source_staging_id: editStaging.id,
                  root_logical_path: childPath,
                },
              )
              : child;
            setChildEditSessions((current) => ({ ...current, [revisionId]: recovered }));
            setEditOccurrences((current) => setOccurrenceCheckoutStatus(
              current, revisionId, "CHECKED_OUT_BY_ME",
            ));
            prepared.add(revisionId);
            continue;
          }
          throw new Error(
            `Select the assembly package again to finish preparing ${childPath}. Existing staged work was preserved.`,
          );
        }
        let staged = child;
        if (child.files.length > 0) {
          const sourceHash = await fileSha256(source);
          if (!child.files.some((file) => file.sha256 === sourceHash && file.state === "VERIFIED")) {
            throw new Error(
              `${occurrence.child_item.item_number} already has different staged content. Continue or cancel that child edit first.`,
            );
          }
        } else {
          staged = await stageCadFile(child, source, occurrence.child_item.item_type);
        }
        if (nestedChanges.length > 0 && staged.proposal?.bom) {
          const identityFileByOccurrence = new Map<string, { fileId: string; decision: any }>();
          for (const change of nestedChanges.filter((candidate) =>
            ["ADDED", "RELINKED"].includes(candidate.status))) {
            const decisionKey = `${childPath.toLowerCase()}::${change.occurrence_key.toLowerCase()}`;
            const decision = editAssemblyAnalysis?.identity_decisions?.[decisionKey];
            if (!decision) throw new Error(`Resolve identity for ${change.occurrence_key} first.`);
            const referencedPath = String(change.new?.child_logical_path ?? "");
            const referencedSource = sources.get(referencedPath.toLowerCase());
            const manifest = editAssemblyAnalysis?.files.find((file) =>
              file.logical_path.toLowerCase() === referencedPath.toLowerCase());
            if (!referencedSource || !manifest) {
              throw new Error(`The selected package is missing ${referencedPath}.`);
            }
            let nestedFile = staged.files.find((file) =>
              file.logical_path.toLowerCase() === referencedPath.toLowerCase());
            if (!nestedFile) {
              staged = await api<StagingResult>(`/staging/${staged.id}/files`, "POST", {
                expected_snapshot_version: staged.snapshot_version,
                logical_path: referencedPath,
                filename: manifest.filename,
                byte_size: manifest.byte_size,
                sha256: manifest.sha256,
                mime_type: referencedSource.type || "application/octet-stream",
              });
              nestedFile = staged.files.find((file) =>
                file.logical_path.toLowerCase() === referencedPath.toLowerCase());
            }
            if (!nestedFile) throw new Error(`Unable to stage ${referencedPath}.`);
            if (nestedFile.state !== "VERIFIED") {
              const upload = await fetch(serverApiUrl(nestedFile.upload_url), {
                method: "PUT",
                headers: { ...authorizationHeader(), "Content-Type": referencedSource.type || "application/octet-stream" },
                body: referencedSource,
              });
              if (!upload.ok) throw new Error(`Upload failed for ${referencedPath} (${upload.status}).`);
              staged = await upload.json() as StagingResult;
              nestedFile = staged.files.find((file) => file.id === nestedFile!.id)!;
            }
            identityFileByOccurrence.set(change.occurrence_key.toLowerCase(), {
              fileId: nestedFile.id, decision,
            });
          }
          const byOccurrence = new Map(nestedChanges.map((candidate) => [
            candidate.occurrence_key.toLowerCase(), candidate,
          ]));
          const reconciledBom = staged.proposal.bom.flatMap((line: Record<string, any>) => {
            const change = byOccurrence.get(String(line.instance_name ?? "").toLowerCase());
            if (!change) return [line];
            if (change.status === "REMOVED") return [];
            if (change.status === "RELINKED") {
              const identity = identityFileByOccurrence.get(change.occurrence_key.toLowerCase());
              if (!identity) return [line];
              const reference = change.new ?? {};
              return [{
                ...line,
                child_revision_id: null,
                child_staged_file_id: identity.fileId,
                source_reference: reference.source_reference || reference.child_logical_path,
                reference_designator: reference.reference_designator || reference.instance_name,
                transform: reference.transform ?? line.transform,
                is_suppressed: reference.is_suppressed ?? false,
                is_visible: reference.is_visible ?? true,
                properties: reference.properties ?? {},
              }];
            }
            if (!["UNCHANGED", "MOVED_RENAMED"].includes(change.status)) return [line];
            const reference = change.new ?? {};
            return [{
              ...line,
              instance_name: reference.occurrence_key || reference.occurrence_path
                || reference.occurrence_id || line.instance_name,
              source_reference: reference.source_reference || reference.child_logical_path,
              reference_designator: reference.reference_designator || reference.instance_name,
              transform: reference.transform ?? line.transform,
              is_suppressed: reference.is_suppressed ?? false,
              is_visible: reference.is_visible ?? true,
              properties: reference.properties ?? {},
            }];
          });
          for (const change of nestedChanges.filter((candidate) => candidate.status === "ADDED")) {
            const identity = identityFileByOccurrence.get(change.occurrence_key.toLowerCase());
            if (!identity) continue;
            const reference = change.new ?? {};
            reconciledBom.push({
              parent_revision_id: revisionId,
              parent_staged_file_id: null,
              child_revision_id: null,
              child_staged_file_id: identity.fileId,
              instance_name: change.occurrence_key,
              quantity: 1,
              source_reference: reference.source_reference || reference.child_logical_path,
              reference_designator: reference.reference_designator || reference.instance_name,
              transform: reference.transform,
              is_suppressed: reference.is_suppressed ?? false,
              is_visible: reference.is_visible ?? true,
              properties: reference.properties ?? {},
            });
          }
          const artifacts = [...staged.proposal.artifacts];
          const primaryArtifact = artifacts.find((artifact: any) =>
            artifact.is_primary && !artifact.existing_relation_id);
          const identityDecisions = [...(staged.proposal.identity_decisions ?? [])];
          const decidedFileIds = new Set(identityDecisions.map((decision: any) => decision.staged_file_id));
          for (const { fileId, decision } of identityFileByOccurrence.values()) {
            if (primaryArtifact && !primaryArtifact.files.some((file: any) => file.staged_file_id === fileId)) {
              const stagedFile = staged.files.find((file) => file.id === fileId)!;
              primaryArtifact.files.push({
                staged_file_id: fileId,
                reference_name: stagedFile.logical_path,
                role: "SECONDARY",
              });
            }
            if (!decidedFileIds.has(fileId)) {
              identityDecisions.push(decision.decision === "USE_EXISTING" ? {
                staged_file_id: fileId,
                decision: "USE_EXISTING",
                existing_revision_id: decision.existing_revision_id,
              } : {
                staged_file_id: fileId,
                decision: "CREATE_NEW",
                item: decision.item,
                revision: decision.revision,
              });
              decidedFileIds.add(fileId);
            }
            if (decision.decision === "CREATE_NEW"
              && !artifacts.some((artifact: any) => artifact.owner_staged_file_id === fileId)) {
              const stagedFile = staged.files.find((file) => file.id === fileId)!;
              artifacts.push({
                client_artifact_id: randomUUID(),
                existing_relation_id: null,
                owner_staged_file_id: fileId,
                artifact_type: decision.item?.item_type === "ASSEMBLY" ? "CAD_ASSEMBLY" : "CAD_MODEL",
                name: decision.item?.name || stagedFile.filename,
                description: null,
                classification: "AUTHORITATIVE",
                relation_type: "SPECIFICATION",
                sequence: artifacts.length,
                is_primary: true,
                files: [{ staged_file_id: fileId, reference_name: "MODEL", role: "PRIMARY" }],
              });
            }
          }
          const stagedFileByPath = new Map(staged.files.map((file) => [
            file.logical_path.toLowerCase(), file,
          ]));
          const ensureNestedFile = async (logicalPath: string) => {
            let nestedFile = stagedFileByPath.get(logicalPath.toLowerCase());
            if (nestedFile) return nestedFile;
            const manifest = editAssemblyAnalysis?.files.find((file) =>
              file.logical_path.toLowerCase() === logicalPath.toLowerCase());
            const nestedSource = sources.get(logicalPath.toLowerCase());
            if (!manifest || !nestedSource) throw new Error(`The selected package is missing ${logicalPath}.`);
            staged = await api<StagingResult>(`/staging/${staged.id}/files`, "POST", {
              expected_snapshot_version: staged.snapshot_version,
              logical_path: logicalPath,
              filename: manifest.filename,
              byte_size: manifest.byte_size,
              sha256: manifest.sha256,
              mime_type: nestedSource.type || "application/octet-stream",
            });
            nestedFile = staged.files.find((file) =>
              file.logical_path.toLowerCase() === logicalPath.toLowerCase());
            if (!nestedFile) throw new Error(`Unable to stage ${logicalPath}.`);
            const upload = await fetch(serverApiUrl(nestedFile.upload_url), {
              method: "PUT",
              headers: { ...authorizationHeader(), "Content-Type": nestedSource.type || "application/octet-stream" },
              body: nestedSource,
            });
            if (!upload.ok) throw new Error(`Upload failed for ${logicalPath} (${upload.status}).`);
            staged = await upload.json() as StagingResult;
            nestedFile = staged.files.find((file) => file.id === nestedFile!.id)!;
            stagedFileByPath.set(logicalPath.toLowerCase(), nestedFile);
            return nestedFile;
          };
          const assemblyQueue = [...identityFileByOccurrence.entries()].flatMap(([key, value]) => {
            if (value.decision.decision !== "CREATE_NEW"
              || value.decision.item?.item_type !== "ASSEMBLY") return [];
            const change = nestedChanges.find((candidate) => candidate.occurrence_key.toLowerCase() === key);
            const path = String(change?.new?.child_logical_path ?? "");
            return path ? [{ path, fileId: value.fileId }] : [];
          });
          const visitedAssemblyPaths = new Set<string>();
          while (assemblyQueue.length > 0) {
            const owner = assemblyQueue.shift()!;
            if (visitedAssemblyPaths.has(owner.path.toLowerCase())) continue;
            visitedAssemblyPaths.add(owner.path.toLowerCase());
            const ownerArtifact = artifacts.find((artifact: any) =>
              artifact.owner_staged_file_id === owner.fileId);
            for (const reference of dependencyReport?.references?.filter((candidate) =>
              candidate.parent_logical_path.toLowerCase() === owner.path.toLowerCase()) ?? []) {
              const change = editReconciliation.find((candidate) =>
                String(candidate.parent_logical_path ?? "").toLowerCase() === owner.path.toLowerCase()
                && candidate.occurrence_key.toLowerCase() === String(reference.occurrence_key
                  || reference.occurrence_path || reference.occurrence_id
                  || reference.instance_name || "").toLowerCase());
              if (!change) throw new Error(`No reconciliation result exists for ${reference.child_logical_path}.`);
              const decisionKey = `${owner.path.toLowerCase()}::${change.occurrence_key.toLowerCase()}`;
              const decision = editAssemblyAnalysis?.identity_decisions?.[decisionKey];
              if (!decision) throw new Error(`Resolve identity for ${change.occurrence_key} first.`);
              const descendantFile = await ensureNestedFile(reference.child_logical_path);
              if (primaryArtifact && !primaryArtifact.files.some((file: any) =>
                file.staged_file_id === descendantFile.id)) {
                primaryArtifact.files.push({
                  staged_file_id: descendantFile.id,
                  reference_name: reference.child_logical_path,
                  role: "SECONDARY",
                });
              }
              if (ownerArtifact && !ownerArtifact.files.some((file: any) =>
                file.staged_file_id === descendantFile.id)) {
                ownerArtifact.files.push({
                  staged_file_id: descendantFile.id,
                  reference_name: reference.child_logical_path,
                  role: "SECONDARY",
                });
              }
              if (!decidedFileIds.has(descendantFile.id)) {
                identityDecisions.push(decision.decision === "USE_EXISTING" ? {
                  staged_file_id: descendantFile.id,
                  decision: "USE_EXISTING",
                  existing_revision_id: decision.existing_revision_id,
                } : {
                  staged_file_id: descendantFile.id,
                  decision: "CREATE_NEW",
                  item: decision.item,
                  revision: decision.revision,
                });
                decidedFileIds.add(descendantFile.id);
              }
              if (decision.decision === "CREATE_NEW"
                && !artifacts.some((artifact: any) => artifact.owner_staged_file_id === descendantFile.id)) {
                artifacts.push({
                  client_artifact_id: randomUUID(),
                  existing_relation_id: null,
                  owner_staged_file_id: descendantFile.id,
                  artifact_type: decision.item?.item_type === "ASSEMBLY" ? "CAD_ASSEMBLY" : "CAD_MODEL",
                  name: decision.item?.name || descendantFile.filename,
                  description: null,
                  classification: "AUTHORITATIVE",
                  relation_type: "SPECIFICATION",
                  sequence: artifacts.length,
                  is_primary: true,
                  files: [{ staged_file_id: descendantFile.id, reference_name: "MODEL", role: "PRIMARY" }],
                });
              }
              reconciledBom.push({
                parent_revision_id: null,
                parent_staged_file_id: owner.fileId,
                child_revision_id: null,
                child_staged_file_id: descendantFile.id,
                instance_name: change.occurrence_key,
                quantity: 1,
                source_reference: reference.source_reference || reference.child_logical_path,
                reference_designator: reference.reference_designator || reference.instance_name,
                transform: reference.transform,
                is_suppressed: reference.is_suppressed ?? false,
                is_visible: reference.is_visible ?? true,
                properties: reference.properties ?? {},
              });
              if (decision.decision === "CREATE_NEW" && decision.item?.item_type === "ASSEMBLY") {
                assemblyQueue.push({ path: reference.child_logical_path, fileId: descendantFile.id });
              }
            }
          }
          staged = await api<StagingResult>(`/staging/${staged.id}/proposal`, "PUT", {
            ...staged.proposal,
            expected_snapshot_version: staged.snapshot_version,
            artifacts,
            identity_decisions: identityDecisions,
            bom: reconciledBom,
          });
        }
        if (occurrence.child_item.item_type === "ASSEMBLY") {
          staged = await api<StagingResult>(
            `/staging/${staged.id}/assembly-reconciliation/adopt`,
            "POST",
            {
              expected_snapshot_version: staged.snapshot_version,
              source_staging_id: editStaging.id,
              root_logical_path: childPath,
            },
          );
        }
        setChildEditSessions((current) => ({ ...current, [revisionId]: staged }));
        setEditOccurrences((current) => setOccurrenceCheckoutStatus(
          current, revisionId, "CHECKED_OUT_BY_ME",
        ));
        prepared.add(revisionId);
      }
      setEditMessage(`${prepared.size} modified component checkout${prepared.size === 1 ? "" : "s"} prepared.`);
    } catch (reason) {
      setEditStagingError(reason instanceof Error ? reason.message : "Unable to prepare modified components.");
    } finally {
      setEditBusy(false);
    }
  };
  return prepareModifiedEditChildren;
}
