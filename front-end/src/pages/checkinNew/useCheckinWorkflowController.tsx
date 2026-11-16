import { type ChangeEvent, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  api,
  authorizationHeader,
  serverApiUrl,
  type AIActionExecution,
  type AIActionProposal,
  type ValidationAnalysis,
  type ValidationReport,
} from '../../workflow-api';
import '../CheckinNew.css';
import { createPrepareModifiedEditChildren } from './editAssemblyActions';
import { createAssemblyUploadActions } from './assemblyUploadActions';
import { randomUUID } from '../../uuid';
import { unzipSync, zipSync } from 'fflate';

import {
  type CadBundleManifest,
  type CadDependencyReport,
  type CommitProcessingJob,
  type EditOccurrence,
  type EditWhereUsed,
  type DuplicateMatch,
  type FileEntry,
  type GeneratedItemNumber,
  type ItemNumberTemplateOption,
  type StagingCommitResult,
  type StagingResult,
  type StagingWorkflowResult,
  type WorkerPreflight,
  cadProfile,
  checkIdentities,
  createsManagedItem,
  fileSha256,
  isDrawing,
  isProcessingSupport,
  logicalPathDepth,
  setOccurrenceCheckoutStatus,
  withoutProcessingSupport,
} from './model';

export function useCheckinWorkflowController({
  embedded = false,
  editContext,
}: {
  embedded?: boolean;
  editContext?: {
    itemId: string;
    itemNumber: string;
    itemName: string;
    itemType: string;
    revisionId: string;
    revisionCode: string;
  };
} = {}) {
  const [searchParams] = useSearchParams();
  const targetItemId = searchParams.get('itemId') ?? editContext?.itemId ?? null;
  const targetItemNumber = searchParams.get('itemNumber') ?? editContext?.itemNumber ?? '';
  const targetItemName = searchParams.get('itemName') ?? editContext?.itemName ?? '';
  const targetItemType = searchParams.get('itemType') ?? editContext?.itemType ?? 'PART';
  const targetRevision = searchParams.get('revision') ?? editContext?.revisionCode ?? 'A';
  const targetRevisionId = searchParams.get('revisionId') ?? editContext?.revisionId ?? null;
  const sourceSnapshotId = searchParams.get('sourceSnapshotId');
  const resumeStageId = searchParams.get('resumeStageId');
  const resumeTaskName = searchParams.get('taskName') ?? '';
  const editMode = searchParams.get('mode') === 'edit' || !!editContext;
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [numberTemplates, setNumberTemplates] = useState<ItemNumberTemplateOption[]>([]);
  const [selectedNumberTemplateId, setSelectedNumberTemplateId] = useState('');
  const [identityBusy, setIdentityBusy] = useState(false);
  const [uploadMode, setUploadMode] = useState<'single' | 'folder'>(
    targetItemType === 'ASSEMBLY' ? 'folder' : 'single'
  );
  const [mainFileName, setMainFileName] = useState('');
  const [selectedAssemblyPath, setSelectedAssemblyPath] = useState('');
  const [fileSelectionError, setFileSelectionError] = useState('');
  const [workerWarning, setWorkerWarning] = useState('');
  const [dependencyReport, setDependencyReport] = useState<CadDependencyReport | null>(null);
  const [workerAnalysisFiles, setWorkerAnalysisFiles] = useState<File[]>([]);
  const [cadSelectionBusy, setCadSelectionBusy] = useState(false);
  const [cadSelectionStatus, setCadSelectionStatus] = useState('');
  const [stagingId, setStagingId] = useState<string | null>(null);
  const [stagingSnapshotVersion, setStagingSnapshotVersion] = useState<number | null>(null);
  const [resumeStage, setResumeStage] = useState<StagingResult | null>(null);
  const [resumeStageLoading, setResumeStageLoading] = useState(Boolean(resumeStageId));
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [validationHistory, setValidationHistory] = useState<ValidationReport[]>([]);
  const [analysis, setAnalysis] = useState<ValidationAnalysis | null>(null);
  const [validationBusy, setValidationBusy] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [actionProposal, setActionProposal] = useState<AIActionProposal | null>(null);
  const [actionExecution, setActionExecution] = useState<AIActionExecution | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [editStaging, setEditStaging] = useState<StagingResult | null>(null);
  const [editStagingError, setEditStagingError] = useState('');
  const [editClientStagingId] = useState(() => randomUUID());
  const [editBusy, setEditBusy] = useState(false);
  const [editMessage, setEditMessage] = useState('');
  const [commitMessage, setCommitMessage] = useState('');
  const [committedItemId, setCommittedItemId] = useState<string | null>(null);
  const [committedRevisionId, setCommittedRevisionId] = useState<string | null>(null);
  const [commitProcessingJobs, setCommitProcessingJobs] = useState<CommitProcessingJob[]>([]);
  const [commitProcessingLoaded, setCommitProcessingLoaded] = useState(false);
  const [commitProcessingError, setCommitProcessingError] = useState('');
  const [commitProcessingRefresh, setCommitProcessingRefresh] = useState(0);
  const [editWhereUsed, setEditWhereUsed] = useState<EditWhereUsed | null>(null);
  const editStagingRequestKey = useRef<string | null>(null);
  const [editContentMenu, setEditContentMenu] = useState<{
    artifact: any;
    file?: any;
    x: number;
    y: number;
  } | null>(null);
  const [editOccurrences, setEditOccurrences] = useState<EditOccurrence[]>([]);
  const [collapsedEditNodes, setCollapsedEditNodes] = useState<Set<string>>(new Set());
  const [editReconciliationFilter, setEditReconciliationFilter] = useState<
    'ALL' | 'CHANGED' | 'ACTION' | 'CONFLICT' | 'NEW' | 'REMOVED'
  >('ALL');
  const [showEditReview, setShowEditReview] = useState(false);
  const [childEditSessions, setChildEditSessions] = useState<Record<string, StagingResult>>({});
  const [editOccurrenceMenu, setEditOccurrenceMenu] = useState<{
    occurrence: EditOccurrence;
    x: number;
    y: number;
  } | null>(null);
  const toggleEditNode = (nodeKey: string) => {
    setCollapsedEditNodes((current) => {
      const next = new Set(current);
      if (next.has(nodeKey)) next.delete(nodeKey);
      else next.add(nodeKey);
      return next;
    });
  };
  const visibleEditOccurrences = (
    occurrences: EditOccurrence[],
    depth = 0
  ): Array<{
    occurrence: EditOccurrence;
    depth: number;
  }> =>
    occurrences.flatMap((occurrence) => [
      { occurrence, depth },
      ...(collapsedEditNodes.has(occurrence.id)
        ? []
        : visibleEditOccurrences(occurrence.children ?? [], depth + 1)),
    ]);

  const stagedMainFileName = resumeStage?.files.length
    ? [...resumeStage.files].sort((left, right) => {
        const depth = (path: string) => (path.match(/[\\/]/g) ?? []).length;
        return (
          depth(left.logical_path) - depth(right.logical_path) ||
          left.logical_path.localeCompare(right.logical_path)
        );
      })[0].filename
    : '';
  const resumeDisplayName = resumeTaskName || stagedMainFileName;
  const editAssemblyAnalysis = editStaging?.result?.assembly_analysis;
  const editReconciliation = editAssemblyAnalysis?.reconciliation ?? [];
  const editAnalysisRows = editAssemblyAnalysis?.root_file_reconciliation
    ? [editAssemblyAnalysis.root_file_reconciliation, ...editReconciliation]
    : editReconciliation;
  const editAnalysisIsCurrent = Boolean(
    editAssemblyAnalysis && editAssemblyAnalysis.snapshot_version === editStaging?.snapshot_version
  );
  const unresolvedEditIdentities = editReconciliation.filter((entry) => {
    if (!['ADDED', 'RELINKED'].includes(entry.status)) return false;
    const key = `${String(entry.parent_logical_path ?? '').toLowerCase()}::${entry.occurrence_key.toLowerCase()}`;
    return !editAssemblyAnalysis?.identity_decisions?.[key];
  });
  const modifiedEditReferences = editReconciliation.filter(
    (entry) =>
      entry.status === 'MODIFIED' &&
      Boolean(entry.old?.child_revision_id) &&
      Boolean(entry.new?.child_logical_path)
  );
  const filteredEditReconciliation = editAnalysisRows.filter((entry) => {
    if (editReconciliationFilter === 'ALL') return true;
    if (editReconciliationFilter === 'CHANGED') return entry.status !== 'UNCHANGED';
    if (editReconciliationFilter === 'ACTION')
      return ['MODIFIED', 'ADDED', 'RELINKED', 'MISSING', 'CONFLICT'].includes(entry.status);
    if (editReconciliationFilter === 'CONFLICT')
      return ['MISSING', 'CONFLICT'].includes(entry.status);
    if (editReconciliationFilter === 'NEW') return entry.status === 'ADDED';
    return entry.status === 'REMOVED';
  });
  const editReconciliationCounts = editAnalysisRows.reduce<Record<string, number>>(
    (counts, entry) => {
      counts[entry.status] = (counts[entry.status] ?? 0) + 1;
      return counts;
    },
    {}
  );
  const unpreparedModifiedReferences = modifiedEditReferences.filter(
    (entry) => !childEditSessions[String(entry.old?.child_revision_id ?? '')]
  );
  const editBlockingCount =
    editReconciliation.filter((entry) => ['MISSING', 'CONFLICT'].includes(entry.status)).length +
    unresolvedEditIdentities.length +
    unpreparedModifiedReferences.length;
  const editIdentityDecisions = Object.values(editAssemblyAnalysis?.identity_decisions ?? {});
  const editCreateNewCount = editIdentityDecisions.filter(
    (decision) => decision.decision === 'CREATE_NEW'
  ).length;
  const editReuseCount = editIdentityDecisions.filter(
    (decision) => decision.decision === 'USE_EXISTING'
  ).length;
  const editAssemblyReviewBlocked = Boolean(
    editAssemblyAnalysis &&
    (!editAnalysisIsCurrent || !editAssemblyAnalysis.package_staged || editBlockingCount > 0)
  );

  useEffect(() => {
    if (!resumeStageId) return;
    setResumeStageLoading(true);
    api<StagingResult>(`/staging/${resumeStageId}`)
      .then((stage) => {
        setResumeStage(stage);
        setStagingId(stage.id);
        setStagingSnapshotVersion(stage.snapshot_version);
        setUploadMode(stage.files.length > 1 ? 'folder' : 'single');
      })
      .catch((reason) =>
        setFileSelectionError(
          reason instanceof Error ? reason.message : 'Unable to load the saved upload stage.'
        )
      )
      .finally(() => setResumeStageLoading(false));
  }, [resumeStageId]);

  useEffect(() => {
    if (!committedRevisionId) return;
    let cancelled = false;
    let timer: number | undefined;

    const loadProcessing = async () => {
      try {
        const jobs = await api<CommitProcessingJob[]>(
          `/revisions/${committedRevisionId}/processing-jobs`
        );
        if (cancelled) return;
        setCommitProcessingJobs(jobs);
        setCommitProcessingLoaded(true);
        setCommitProcessingError('');
        const finished =
          jobs.length > 0 && jobs.every((job) => ['SUCCEEDED', 'FAILED'].includes(job.state));
        if (!finished) timer = window.setTimeout(() => void loadProcessing(), 2000);
      } catch (reason) {
        if (cancelled) return;
        setCommitProcessingLoaded(true);
        setCommitProcessingError(
          reason instanceof Error ? reason.message : 'Unable to load processing status.'
        );
        timer = window.setTimeout(() => void loadProcessing(), 5000);
      }
    };

    void loadProcessing();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [committedRevisionId, commitProcessingRefresh]);

  useEffect(() => {
    if (targetItemId) return;
    api<ItemNumberTemplateOption[]>('/item-number-templates')
      .then((templates) => {
        setNumberTemplates(templates);
        setSelectedNumberTemplateId(
          templates.find((template) => template.is_selected)?.id ?? templates[0]?.id ?? ''
        );
      })
      .catch(() => setNumberTemplates([]));
  }, [targetItemId]);

  useEffect(() => {
    if (!editContentMenu) return;
    const close = () => setEditContentMenu(null);
    window.addEventListener('click', close);
    window.addEventListener('blur', close);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('blur', close);
    };
  }, [editContentMenu]);

  useEffect(() => {
    if (!editMode || !targetItemId || !targetRevisionId) return;
    const requestKey = [targetItemId, targetRevisionId, sourceSnapshotId ?? ''].join(':');
    if (editStagingRequestKey.current === requestKey) return;
    editStagingRequestKey.current = requestKey;
    setEditStagingError('');
    api<StagingResult>(
      `/staging/items/${targetItemId}/revisions/${targetRevisionId}/edit`,
      'POST',
      {
        client_staging_id: editClientStagingId,
        ...(sourceSnapshotId ? { source_snapshot_id: sourceSnapshotId } : {}),
      }
    )
      .then((staging) => {
        setEditStaging(staging);
        setStagingId(staging.id);
        setStagingSnapshotVersion(staging.snapshot_version);
        setDependencyReport(staging.result?.assembly_analysis?.report ?? null);
      })
      .catch((reason: Error) => setEditStagingError(reason.message));
    api<{ occurrences: EditOccurrence[] }>(
      `/items/${targetItemId}/revisions/${targetRevisionId}/bom`
    )
      .then((result) => setEditOccurrences(result.occurrences))
      .catch(() => setEditOccurrences([]));
  }, [editClientStagingId, editMode, sourceSnapshotId, targetItemId, targetRevisionId]);

  useEffect(() => {
    const revisionId = editStaging?.base_revision_id ?? targetRevisionId;
    if (!editMode || !revisionId) return;
    api<EditWhereUsed>(`/revisions/${revisionId}/where-used`)
      .then(setEditWhereUsed)
      .catch(() => setEditWhereUsed(null));
  }, [editMode, editStaging?.base_revision_id, targetRevisionId]);

  useEffect(() => {
    if (!editOccurrenceMenu) return;
    const close = () => setEditOccurrenceMenu(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [editOccurrenceMenu]);

  const checkoutChild = async (occurrence: EditOccurrence) => {
    setEditBusy(true);
    setEditStagingError('');
    try {
      const staging = await api<StagingResult>(
        `/staging/items/${occurrence.child_item.id}/revisions/${occurrence.child_revision.id}/edit`,
        'POST',
        { client_staging_id: randomUUID() }
      );
      setChildEditSessions((current) => ({ ...current, [occurrence.child_revision.id]: staging }));
      setEditOccurrences((current) =>
        setOccurrenceCheckoutStatus(current, occurrence.child_revision.id, 'CHECKED_OUT_BY_ME')
      );
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
      setEditOccurrenceMenu(null);
    }
  };

  const cancelChildCheckout = async (occurrence: EditOccurrence) => {
    const staging = childEditSessions[occurrence.child_revision.id];
    if (!staging) return;
    setEditBusy(true);
    try {
      await api(`/staging/${staging.id}`, 'DELETE');
      setChildEditSessions((current) => {
        const next = { ...current };
        delete next[occurrence.child_revision.id];
        return next;
      });
      setEditOccurrences((current) =>
        setOccurrenceCheckoutStatus(current, occurrence.child_revision.id, 'AVAILABLE')
      );
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
      setEditOccurrenceMenu(null);
    }
  };

  const stageCadFile = async (
    original: StagingResult,
    file: File,
    itemType: string
  ): Promise<StagingResult> => {
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!extension) throw new Error('Choose a CAD file with a file extension.');
    const sha256 = await fileSha256(file);
    let staging = await api<StagingResult>(`/staging/${original.id}/files`, 'POST', {
      expected_snapshot_version: original.snapshot_version,
      logical_path: file.name,
      filename: file.name,
      byte_size: file.size,
      sha256,
      mime_type: file.type || 'application/octet-stream',
    });
    const stagedFile = staging.files.find((row) => row.logical_path === file.name);
    if (!stagedFile) throw new Error('The CAD file was not added to staging.');
    const upload = await fetch(serverApiUrl(stagedFile.upload_url), {
      method: 'PUT',
      headers: {
        ...authorizationHeader(),
        'Content-Type': file.type || 'application/octet-stream',
      },
      body: file,
    });
    if (!upload.ok) throw new Error(`Upload failed (${upload.status}).`);
    staging = (await upload.json()) as StagingResult;
    const profile = cadProfile(file.name, itemType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART');
    const artifactType = profile.artifactType;
    const revision = {
      ...original.proposal.revision,
      cad_system:
        profile.cadSystem === 'Unknown'
          ? original.proposal.revision?.cad_system
          : profile.cadSystem,
      native_format: profile.nativeFormat,
    };
    const retained = (staging.proposal?.artifacts ?? original.proposal?.artifacts ?? []).filter(
      (artifact: any) => !artifact.is_primary
    );
    return api<StagingResult>(`/staging/${staging.id}/proposal`, 'PUT', {
      ...original.proposal,
      expected_snapshot_version: staging.snapshot_version,
      revision,
      artifacts: [
        ...retained,
        {
          client_artifact_id: randomUUID(),
          existing_relation_id: null,
          owner_staged_file_id: null,
          artifact_type: artifactType,
          name: file.name.replace(/\.[^.]+$/, ''),
          classification: 'AUTHORITATIVE',
          relation_type: 'SPECIFICATION',
          sequence: retained.length,
          is_primary: true,
          files: [{ staged_file_id: stagedFile.id, reference_name: 'PRIMARY', role: 'PRIMARY' }],
        },
      ],
    });
  };

  const addEditFile = async (file: File) => {
    if (!editStaging) return;
    setEditBusy(true);
    setEditStagingError('');
    try {
      const staging = await stageCadFile(editStaging, file, targetItemType);
      setEditStaging(staging);
      setStagingSnapshotVersion(staging.snapshot_version);
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
    }
  };

  const checkinChildFile = async (occurrence: EditOccurrence, file: File) => {
    const child = childEditSessions[occurrence.child_revision.id];
    if (!child) return;
    setEditBusy(true);
    setEditStagingError('');
    try {
      const updated = await stageCadFile(child, file, occurrence.child_item.item_type);
      setChildEditSessions((current) => ({
        ...current,
        [occurrence.child_revision.id]: updated,
      }));
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
      setEditOccurrenceMenu(null);
    }
  };

  const downloadEditFile = async (file: { download_url: string; filename: string }) => {
    setEditStagingError('');
    try {
      const response = await fetch(serverApiUrl(file.download_url), {
        headers: authorizationHeader(),
      });
      if (!response.ok) throw new Error(`Download failed (${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    }
  };

  const submitEdit = async () => {
    if (!editStaging) return;
    setEditBusy(true);
    setEditStagingError('');
    try {
      const readyChildren: StagingResult[] = [];
      for (const child of Object.values(childEditSessions)) {
        if (child.files.length === 0) {
          throw new Error('Every checked-out child must have a checked-in CAD file before Submit.');
        }
        const childReport = await api<ValidationReport>(`/staging/${child.id}/validate`, 'POST');
        if (childReport.status === 'BLOCKED' || childReport.status === 'ERROR') {
          throw new Error(
            childReport.findings.find((finding) => finding.blocking)?.message ??
              `A checked-out child has validation status ${childReport.status}.`
          );
        }
        readyChildren.push(
          await api<StagingResult>(`/staging/${child.id}/ready`, 'POST', {
            expected_snapshot_version: child.snapshot_version,
          })
        );
      }
      // Every edit action persists its proposal immediately. Saving it again here would
      // increment the snapshot and invalidate a just-staged assembly analysis.
      const staging = editStaging;
      const report = await api<ValidationReport>(`/staging/${staging.id}/validate`, 'POST');
      setValidation(report);
      if (report.status === 'BLOCKED' || report.status === 'ERROR') {
        throw new Error(
          report.findings.find((finding) => finding.blocking)?.message ??
            `Validation status is ${report.status}.`
        );
      }
      const ready = await api<StagingResult>(`/staging/${staging.id}/ready`, 'POST', {
        expected_snapshot_version: staging.snapshot_version,
      });
      const committed = await api<StagingCommitResult>(
        `/uploads/${editStaging.id}/commit`,
        'POST',
        {
          snapshot_version: ready.snapshot_version,
          commit_id: randomUUID(),
          child_staging_ids: readyChildren.map((child) => child.id),
        }
      );
      setChildEditSessions({});
      setCommittedItemId(committed.item_id);
      setCommittedRevisionId(committed.revision_id);
      setCommitProcessingJobs([]);
      setCommitProcessingLoaded(false);
      setShowEditReview(false);
      setEditMessage('The root revision and all checked-out children were committed.');
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
    }
  };

  const retryCommitProcessing = async (jobId: string) => {
    setCommitProcessingError('');
    try {
      await api(`/processing-jobs/${jobId}/retry`, 'POST');
      setCommitProcessingLoaded(false);
      setCommitProcessingRefresh((value) => value + 1);
    } catch (reason) {
      setCommitProcessingError(
        reason instanceof Error ? reason.message : 'Unable to retry processing.'
      );
    }
  };

  const cancelEdit = async () => {
    if (!editStaging) return;
    setEditBusy(true);
    try {
      await Promise.all(
        Object.values(childEditSessions).map((child) => api(`/staging/${child.id}`, 'DELETE'))
      );
      await api(`/staging/${editStaging.id}`, 'DELETE');
      setEditMessage('Editing was cancelled and the checkout was released.');
      setChildEditSessions({});
      setEditStaging(null);
    } catch (reason) {
      setEditStagingError((reason as Error).message);
    } finally {
      setEditBusy(false);
    }
  };

  const handleFileSelect = async (
    event: ChangeEvent<HTMLInputElement> | File[],
    packagedMain?: string,
    packagedReport?: CadDependencyReport,
    forceFolder = false,
    externalCadSystem?: string
  ) => {
    const selectedFiles = Array.isArray(event) ? event : Array.from(event.target.files ?? []);
    if (selectedFiles.length === 0) return;

    setFileSelectionError('');
    setWorkerWarning('');
    setDependencyReport(null);
    const selected = Array.from(selectedFiles);
    const effectiveMode =
      forceFolder || (packagedMain && packagedReport?.document_type === 'ASSEMBLY')
        ? 'folder'
        : 'single';
    setUploadMode(effectiveMode);
    const hasFreeCad = selected.some((file) => /\.fcstd$/i.test(file.name));
    if (effectiveMode === 'folder' && !hasFreeCad && !externalCadSystem) {
      setFiles([]);
      setMainFileName('');
      setFileSelectionError(
        'Choose a FreeCAD assembly or a format supported by a registered external worker.'
      );
      if (!Array.isArray(event)) event.target.value = '';
      return;
    }
    const chosenFiles = selected.filter((file) => {
      const logicalPath = file.webkitRelativePath || file.name;
      const packagedType = packagedReport?.files.find(
        (row) => row.logical_path === logicalPath
      )?.document_type;
      if (externalCadSystem) return true;
      if (isProcessingSupport(logicalPath, packagedType)) return false;
      return effectiveMode !== 'folder' || /\.fcstd$/i.test(file.name);
    });
    if (chosenFiles.length === 0) return;
    const entries: FileEntry[] = chosenFiles.map((file) => ({
      file,
      logicalPath: file.webkitRelativePath || file.name,
      externalCadSystem,
      documentType:
        externalCadSystem && forceFolder && !packagedReport
          ? (file.webkitRelativePath || file.name) === packagedMain
            ? 'ASSEMBLY'
            : 'SUPPORTING'
          : packagedReport?.files.find(
              (row) => row.logical_path === (file.webkitRelativePath || file.name)
            )?.document_type,
      sha256: null,
      status: 'pending' as const,
      isDuplicate: false,
      itemNumber: '',
      itemName: targetItemName || file.name.replace(/\.[^.]+$/, ''),
      description: '',
      revision: targetRevision,
      category: '',
      uploadProgress: 0,
    }));
    if (targetItemId) {
      entries.forEach((entry) => {
        entry.itemNumber = targetItemNumber;
        entry.itemName = targetItemName;
      });
    }
    const initialMain = packagedMain ?? entries[0]?.logicalPath ?? '';
    setMainFileName(initialMain);
    setSelectedAssemblyPath(initialMain);

    setFiles(entries);
    const extension = initialMain.split('.').pop()?.toLowerCase();
    const requiredWorker =
      extension === 'fcstd'
        ? 'FREECAD'
        : extension === 'step' || extension === 'stp'
          ? 'STEP'
          : null;
    if (!externalCadSystem && requiredWorker) {
      try {
        const preflight = await api<WorkerPreflight>(
          `/workers/preflight?worker_type=${requiredWorker}`
        );
        if (!preflight.available || !preflight.compatible || preflight.status === 'DEGRADED') {
          setWorkerWarning(preflight.message);
        }
      } catch {
        setWorkerWarning(
          `Unable to verify ${requiredWorker} worker availability. You may continue staging, but processing could be delayed.`
        );
      }
    }
    setStagingId(null);
    setStagingSnapshotVersion(null);
    setValidation(null);
    setValidationHistory([]);
    setAnalysis(null);
    setValidationError(null);
    setActionProposal(null);
    setActionExecution(null);
    setCommitMessage('');
    setCommittedItemId(null);

    // Calculate SHA256 for all files
    for (let i = 0; i < entries.length; i++) {
      const entry = entries[i];
      entries[i].status = 'hashing';
      setFiles([...entries]);

      try {
        const sha256 = await fileSha256(entry.file);
        entries[i].sha256 = sha256;
        entries[i].status = 'checking';
        setFiles([...entries]);
      } catch (error) {
        entries[i].status = 'error';
        entries[i].errorMessage = `Hash calculation failed: ${(error as Error).message}`;
        setFiles([...entries]);
      }
    }

    // Check for duplicates
    const hashChecks = entries
      .filter((e) => e.sha256)
      .map((e) => ({
        filename: e.file.name,
        logicalPath: e.logicalPath,
        byte_size: e.file.size,
        sha256: e.sha256!,
      }));

    if (hashChecks.length > 0) {
      try {
        let staging = resumeStageId
          ? await api<StagingResult>(`/staging/${resumeStageId}`)
          : await api<StagingResult>('/staging', 'POST', {
              client_staging_id: randomUUID(),
              ...(targetItemId && targetRevisionId
                ? {
                    operation: 'CREATE_REVISION',
                    item_id: targetItemId,
                    base_revision_id: targetRevisionId,
                  }
                : {}),
              files: hashChecks.map((file) => ({
                logical_path: file.logicalPath,
                filename: file.filename,
                byte_size: file.byte_size,
                sha256: file.sha256,
                mime_type: 'application/octet-stream',
              })),
            });
        if (!resumeStageId && packagedReport?.part_metadata) {
          const rootEntry = entries.find((entry) => entry.logicalPath === initialMain);
          if (
            !rootEntry?.sha256 ||
            packagedReport.metadata_source_sha256?.toLowerCase() !== rootEntry.sha256.toLowerCase()
          ) {
            throw new Error(
              'Local CAD metadata does not match the selected root file. Analyze the CAD file again.'
            );
          }
          staging = await api<StagingResult>(`/staging/${staging.id}/metadata`, 'POST', {
            metadata: packagedReport.part_metadata,
            source_logical_path: rootEntry.logicalPath,
            source_sha256: rootEntry.sha256,
            analyzer: packagedReport.analyzer_version || 'modelryx-local-cad-agent',
          });
          const rootCandidates: DuplicateMatch[] = staging.duplicate_check.candidates.map(
            (candidate) => ({
              item_id: candidate.candidate_item_id,
              item_number: candidate.item_number,
              item_name: candidate.item_number,
              item_type: packagedReport.document_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
              revision_id: candidate.candidate_revision_id,
              revision_code: candidate.revision,
              duplicate_check_id: staging.duplicate_check.id,
              duplicate_candidate_id: candidate.id,
              classification: candidate.classification,
            })
          );
          const root = entries.find((entry) => entry.logicalPath === initialMain);
          if (rootCandidates.length && root) root.duplicates = rootCandidates;
        }
        if (resumeStageId) {
          const stagedByPath = new Map(staging.files.map((file) => [file.logical_path, file]));
          const mismatch = hashChecks.find((file) => {
            const staged = stagedByPath.get(file.logicalPath);
            return !staged || staged.byte_size !== file.byte_size || staged.sha256 !== file.sha256;
          });
          if (mismatch || stagedByPath.size !== hashChecks.length) {
            throw new Error(
              'The selected files do not match this saved stage. Select the same file or folder used when the upload began.'
            );
          }
          const decisions = new Map<string, any>(
            (staging.proposal?.identity_decisions ?? []).map((decision: any) => [
              decision.staged_file_id,
              decision,
            ])
          );
          for (const entry of entries) {
            const staged = stagedByPath.get(entry.logicalPath);
            const decision = staged ? decisions.get(staged.id) : undefined;
            const isRoot = entry.logicalPath === initialMain;
            const item = decision?.item ?? (isRoot ? staging.proposal?.item : undefined);
            const revision =
              decision?.revision ?? (isRoot ? staging.proposal?.revision : undefined);
            if (item) {
              entry.itemNumber = item.item_number ?? entry.itemNumber;
              entry.itemName = item.name ?? entry.itemName;
              entry.description = item.description ?? entry.description;
              entry.category = item.category ?? entry.category;
            }
            if (revision?.revision_code) entry.revision = revision.revision_code;
            if (decision?.decision === 'USE_EXISTING') {
              entry.duplicateChoice = 'USE_EXISTING';
              entry.selectedRevisionId = decision.existing_revision_id;
            }
          }
        }
        setStagingId(staging.id);
        setStagingSnapshotVersion(staging.snapshot_version);
        if (externalCadSystem) {
          sessionStorage.setItem(
            `modelryx-external-stage:${staging.id}`,
            JSON.stringify({ root: initialMain, cadSystem: externalCadSystem })
          );
        }
        const identityEntries = entries.filter(
          (entry) => entry.sha256 && createsManagedItem(entry, packagedReport)
        );
        const reviews = await checkIdentities(identityEntries);
        for (let i = 0; i < entries.length; i++) {
          if (!entries[i].sha256) continue;
          if (!createsManagedItem(entries[i], packagedReport)) {
            entries[i].duplicates = [];
            entries[i].itemNumber = '';
            entries[i].status = 'ready';
            continue;
          }
          const hashDuplicates =
            reviews.find((row) => row.logical_path === entries[i].logicalPath)?.duplicates ?? [];
          entries[i].duplicates = [
            ...(entries[i].duplicates ?? []),
            ...hashDuplicates.filter(
              (candidate) =>
                !(entries[i].duplicates ?? []).some(
                  (existing) => existing.revision_id === candidate.revision_id
                )
            ),
          ];
          if (!targetItemId && !entries[i].duplicates?.length && !entries[i].itemNumber) {
            const generated = await api<GeneratedItemNumber>(
              '/staging/item-numbers/generate',
              'POST',
              {
                item_type: entries[i].documentType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
                template_id: selectedNumberTemplateId || null,
              }
            );
            entries[i].itemNumber = generated.item_number;
          }
          entries[i].status = 'ready';
        }
        setFiles([...entries]);
      } catch (error) {
        console.error('Duplicate check failed:', error);
        const message =
          error instanceof Error ? error.message : 'Duplicate and identity preflight failed.';
        setFileSelectionError(message);
        // Mark all as ready on error
        for (let i = 0; i < entries.length; i++) {
          if (entries[i].status === 'checking') {
            entries[i].status = 'ready';
            entries[i].formError = message;
          }
        }
        setFiles([...entries]);
      }
    }
  };

  const resumeWithFiles = async (selected: File[]) => {
    const stage =
      resumeStage ?? (resumeStageId ? await api<StagingResult>(`/staging/${resumeStageId}`) : null);
    if (!stage) return;
    const unused = [...selected];
    const matched: File[] = [];
    for (const expected of stage.files) {
      const index = unused.findIndex((file) => {
        const path = (file.webkitRelativePath || file.name).replace(/\\/g, '/');
        return (
          file.size === expected.byte_size &&
          (path === expected.logical_path || path.endsWith(`/${expected.logical_path}`))
        );
      });
      if (index < 0) {
        throw new Error(
          `The selected folder is missing ${expected.logical_path}. Select the original upload folder.`
        );
      }
      const file = unused.splice(index, 1)[0];
      Object.defineProperty(file, 'webkitRelativePath', {
        value: expected.logical_path,
        configurable: true,
      });
      matched.push(file);
    }
    const formats = await api<
      Array<{ cadSystem: string; sourceExtensions: string[]; documentKinds: string[] }>
    >('/workers/external-formats');
    const proposedPrimaryId = stage.proposal?.artifacts
      ?.find((artifact: { is_primary?: boolean }) => artifact.is_primary)
      ?.files?.find((file: { role?: string }) => file.role === 'PRIMARY')?.staged_file_id;
    let savedExternal: { root?: string; cadSystem?: string } = {};
    try {
      savedExternal = JSON.parse(
        sessionStorage.getItem(`modelryx-external-stage:${stage.id}`) || '{}'
      );
    } catch {
      // Older or corrupt browser state must not prevent resuming a server-side stage.
    }
    const proposedMain =
      stage.files.find((file) => file.id === proposedPrimaryId) ??
      stage.files.find((file) => file.logical_path === savedExternal.root);
    const externalRoots = stage.files.flatMap((file) => {
      const extension = `.${file.filename.split('.').pop()?.toLowerCase()}`;
      return formats
        .filter(
          (worker) =>
            worker.sourceExtensions.includes(extension) &&
            (stage.files.length === 1 || worker.documentKinds.includes('assembly'))
        )
        .map((worker) => ({ file, worker }));
    });
    const inferred = externalRoots.length === 1 ? externalRoots[0] : undefined;
    const main =
      proposedMain ??
      inferred?.file ??
      stage.files
        .filter((file) => /\.(iam|ipt|fcstd)$/i.test(file.filename))
        .sort(
          (left, right) =>
            logicalPathDepth(left.logical_path) - logicalPathDepth(right.logical_path) ||
            left.logical_path.localeCompare(right.logical_path)
        )[0] ??
      stage.files[0];
    setResumeStage(stage);
    setUploadMode(stage.files.length > 1 ? 'folder' : 'single');
    const system = stage.proposal?.revision?.cad_system ?? savedExternal.cadSystem;
    const externalSystem =
      formats.find((worker) => worker.cadSystem.toLowerCase() === String(system).toLowerCase())
        ?.cadSystem ?? inferred?.worker.cadSystem;
    await handleFileSelect(
      matched,
      main.logical_path,
      undefined,
      stage.files.length > 1,
      externalSystem
    );
  };

  const persistEditAssemblyAnalysis = async (
    report: CadDependencyReport,
    selectedFiles: File[]
  ) => {
    if (!editStaging) throw new Error('The edit staging session is unavailable.');
    const manifest = await Promise.all(
      selectedFiles.map(async (file) => ({
        logical_path: (file.webkitRelativePath || file.name).replace(/\\/g, '/'),
        filename: file.name,
        byte_size: file.size,
        sha256: await fileSha256(file),
      }))
    );
    const updated = await api<StagingResult>(
      `/staging/${editStaging.id}/assembly-analysis`,
      'PUT',
      { expected_snapshot_version: editStaging.snapshot_version, report, files: manifest }
    );
    setEditStaging(updated);
    setDependencyReport(report);
    setWorkerAnalysisFiles(selectedFiles);
  };

  const importEditCadPackage = async (packageFile: File) => {
    if (!editStaging) return;
    setCadSelectionBusy(true);
    setEditStagingError('');
    try {
      const unpacked = unzipSync(new Uint8Array(await packageFile.arrayBuffer()));
      const manifestBytes = unpacked['manifest.json'];
      if (!manifestBytes)
        throw new Error('This ZIP is not a Modelryx CAD package: manifest.json is missing.');
      let manifest: CadBundleManifest;
      try {
        manifest = JSON.parse(new TextDecoder().decode(manifestBytes)) as CadBundleManifest;
      } catch {
        throw new Error('The CAD package manifest is not valid JSON.');
      }
      if (![1, 2].includes(manifest.schemaVersion) || !Array.isArray(manifest.files)) {
        throw new Error('The CAD package uses an unsupported manifest version.');
      }
      if (targetRevisionId && manifest.revisionId !== targetRevisionId) {
        throw new Error('This CAD package belongs to a different Item Revision.');
      }
      const rootPath =
        manifest.root?.path ??
        manifest.files
          .filter((entry) => entry.revisionId === manifest.revisionId)
          .sort(
            (left, right) =>
              Number(right.role === 'PRIMARY') - Number(left.role === 'PRIMARY') ||
              logicalPathDepth(left.path) - logicalPathDepth(right.path)
          )[0]?.path;
      if (!rootPath) throw new Error('The CAD package does not identify its root file.');

      const selectedFiles: File[] = [];
      const seen = new Set<string>();
      for (const expected of manifest.files) {
        const logicalPath = expected.path.replace(/\\/g, '/').replace(/^\.\//, '');
        if (!logicalPath || logicalPath.startsWith('/') || logicalPath.split('/').includes('..')) {
          throw new Error(`The manifest contains an unsafe path: ${expected.path}`);
        }
        const folded = logicalPath.toLocaleLowerCase();
        if (seen.has(folded)) throw new Error(`The manifest repeats the path ${logicalPath}.`);
        seen.add(folded);
        const bytes = unpacked[logicalPath];
        if (!bytes) throw new Error(`The CAD package is missing ${logicalPath}.`);
        if (bytes.byteLength !== expected.byteSize) {
          throw new Error(`${logicalPath} does not match the size recorded in manifest.json.`);
        }
        const file = new File([bytes], logicalPath.split('/').pop() ?? logicalPath, {
          type: 'application/octet-stream',
        });
        Object.defineProperty(file, 'webkitRelativePath', { value: logicalPath });
        if ((await fileSha256(file)).toLocaleLowerCase() !== expected.sha256.toLocaleLowerCase()) {
          throw new Error(`${logicalPath} does not match the SHA-256 recorded in manifest.json.`);
        }
        selectedFiles.push(file);
      }
      const rootFile = selectedFiles.find((file) => file.webkitRelativePath === rootPath);
      if (!rootFile) throw new Error(`The manifest root file ${rootPath} is missing.`);

      if (!/\.(?:iam|fcstd)$/i.test(rootPath)) {
        await addEditFile(rootFile);
        setEditMessage(`Imported and verified ${rootFile.name} from the CAD package.`);
        return;
      }

      const rootBytes = new TextEncoder().encode(rootPath);
      let rootBinary = '';
      rootBytes.forEach((value) => {
        rootBinary += String.fromCharCode(value);
      });
      const response = await fetch(serverApiUrl('/cad/analyze'), {
        method: 'POST',
        headers: {
          ...authorizationHeader(),
          'Content-Type': 'application/zip',
          'X-Modelryx-Root-Path-B64': btoa(rootBinary),
        },
        body: packageFile,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(body.error ?? `Local CAD package analysis failed (${response.status}).`);
      const report = withoutProcessingSupport(body as CadDependencyReport);
      if (report.missing_references.length > 0) {
        throw new Error(
          `${report.missing_references.length} referenced CAD file(s) are missing from the package.`
        );
      }
      const byPath = new Map(
        selectedFiles.map((file) => [file.webkitRelativePath.toLocaleLowerCase(), file])
      );
      const required = report.files
        .map((entry) => byPath.get(entry.logical_path.toLocaleLowerCase()))
        .filter((file): file is File => Boolean(file));
      if (required.length !== report.files.length) {
        throw new Error('The analyzed CAD references do not match the package manifest.');
      }
      await persistEditAssemblyAnalysis(report, required);
      setEditMessage(`Imported and verified ${required.length} CAD files from the package.`);
    } catch (reason) {
      setEditStagingError(
        reason instanceof Error ? reason.message : 'Unable to import the CAD package.'
      );
    } finally {
      setCadSelectionBusy(false);
    }
  };

  const analyzeExternalEditFolder = async (selected: File[], rootName: string) => {
    if (!editStaging) return;
    setCadSelectionBusy(true);
    setEditStagingError('');
    try {
      const roots = selected.filter((file) => file.name === rootName);
      if (roots.length !== 1)
        throw new Error('The folder must contain exactly one file with the selected root name.');
      const root = roots[0];
      const rootPath = root.webkitRelativePath || root.name;
      const extension = `.${root.name.split('.').pop()?.toLowerCase()}`;
      const system = String(editStaging.proposal?.revision?.cad_system ?? '')
        .toLowerCase()
        .replace(/[^a-z0-9]/g, '');
      const workers = await api<
        Array<{
          id: string;
          cadSystem: string;
          sourceExtensions: string[];
          documentKinds: string[];
          dependencyAnalysis: boolean;
        }>
      >('/workers/external-formats');
      const matches = workers.filter(
        (worker) =>
          worker.dependencyAnalysis &&
          worker.documentKinds.includes('assembly') &&
          worker.sourceExtensions.includes(extension) &&
          worker.cadSystem.toLowerCase().replace(/[^a-z0-9]/g, '') === system
      );
      if (matches.length !== 1)
        throw new Error(
          matches.length
            ? 'More than one external analyzer matches this assembly.'
            : 'No enabled external worker offers dependency analysis for this assembly.'
        );
      if (selected.length > 5000) throw new Error('The assembly folder contains too many files.');
      const contents: Record<string, Uint8Array> = {};
      const paths = new Set<string>();
      for (const file of selected) {
        const path = (file.webkitRelativePath || file.name).replace(/\\/g, '/');
        if (
          !path ||
          path.startsWith('/') ||
          path.split('/').includes('..') ||
          paths.has(path.toLowerCase())
        )
          throw new Error(`Unsafe or duplicate package path: ${path}`);
        paths.add(path.toLowerCase());
        contents[path] = new Uint8Array(await file.arrayBuffer());
      }
      const rootBytes = new TextEncoder().encode(rootPath);
      let binary = '';
      rootBytes.forEach((value) => {
        binary += String.fromCharCode(value);
      });
      const archive = zipSync(contents);
      const response = await fetch(
        serverApiUrl(`/cad/analyze?worker_id=${encodeURIComponent(matches[0].id)}`),
        {
          method: 'POST',
          headers: {
            ...authorizationHeader(),
            'Content-Type': 'application/zip',
            'X-Modelryx-Root-Path-B64': btoa(binary),
          },
          body: new Blob([archive]),
        }
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(body.detail ?? `External assembly analysis failed (${response.status}).`);
      const report = body as CadDependencyReport;
      if (
        report.document_type !== 'ASSEMBLY' ||
        report.root_logical_path?.toLowerCase() !== rootPath.toLowerCase() ||
        !Array.isArray(report.files) ||
        !Array.isArray(report.missing_references)
      )
        throw new Error('The worker returned an invalid assembly analysis.');
      await persistEditAssemblyAnalysis(report, selected);
      setEditMessage(
        `Analyzed ${selected.length} files. Review and reconcile the assembly before staging.`
      );
    } catch (reason) {
      setEditStagingError(
        reason instanceof Error ? reason.message : 'Unable to analyze the external assembly.'
      );
    } finally {
      setCadSelectionBusy(false);
    }
  };

  const applySafeEditReconciliation = async () => {
    if (!editStaging || !editAnalysisIsCurrent) return;
    setEditBusy(true);
    setEditStagingError('');
    try {
      const updated = await api<StagingResult>(
        `/staging/${editStaging.id}/assembly-reconciliation/apply-safe`,
        'POST',
        { expected_snapshot_version: editStaging.snapshot_version }
      );
      setEditStaging(updated);
      setStagingSnapshotVersion(updated.snapshot_version);
      setEditMessage('Safe assembly relationship changes were applied to the staged BOM.');
    } catch (reason) {
      setEditStagingError(
        reason instanceof Error ? reason.message : 'Unable to apply assembly changes.'
      );
    } finally {
      setEditBusy(false);
    }
  };

  const resolveEditAssemblyIdentity = async (
    entry: (typeof editReconciliation)[number],
    choice: string
  ) => {
    if (!editStaging || !entry.parent_logical_path) return;
    setEditBusy(true);
    setEditStagingError('');
    try {
      let decision: Record<string, unknown>;
      if (choice === 'CREATE_NEW') {
        const childPath = String(entry.new?.child_logical_path ?? '');
        const priorEntry = editReconciliation.find(
          (candidate) =>
            candidate !== entry &&
            String(candidate.new?.child_logical_path ?? '').toLowerCase() ===
              childPath.toLowerCase() &&
            editAssemblyAnalysis?.identity_decisions?.[
              `${String(candidate.parent_logical_path ?? '').toLowerCase()}::${candidate.occurrence_key.toLowerCase()}`
            ]
        );
        const priorDecision =
          priorEntry &&
          editAssemblyAnalysis?.identity_decisions?.[
            `${String(priorEntry.parent_logical_path ?? '').toLowerCase()}::${priorEntry.occurrence_key.toLowerCase()}`
          ];
        if (priorDecision && priorDecision.decision !== 'CREATE_NEW') {
          throw new Error(
            'This component was already assigned to an existing Item. Use the same identity for each occurrence.'
          );
        }
        if (priorDecision?.item && priorDecision.revision) {
          decision = {
            decision: 'CREATE_NEW',
            item: priorDecision.item,
            revision: priorDecision.revision,
          };
        } else {
          const analyzedFile = dependencyReport?.files.find(
            (file) => file.logical_path === childPath
          );
          const itemType = analyzedFile?.document_type === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART';
          const generated = await api<GeneratedItemNumber>(
            '/staging/item-numbers/generate',
            'POST',
            {
              item_type: itemType,
            }
          );
          const filename = childPath.split('/').pop() || entry.occurrence_key;
          const profile = cadProfile(filename);
          decision = {
            decision: 'CREATE_NEW',
            item: {
              item_number: generated.item_number,
              name: filename.replace(/\.[^.]+$/, ''),
              item_type: itemType,
              description: null,
              category: null,
            },
            revision: {
              revision_code: 'A',
              description: null,
              cad_system:
                profile.cadSystem === 'Unknown'
                  ? (editStaging.proposal?.revision?.cad_system ?? 'Unknown')
                  : profile.cadSystem,
              native_format: profile.nativeFormat,
            },
          };
        }
      } else {
        decision = { decision: 'USE_EXISTING', existing_revision_id: choice };
      }
      const updated = await api<StagingResult>(
        `/staging/${editStaging.id}/assembly-identity`,
        'PUT',
        {
          expected_snapshot_version: editStaging.snapshot_version,
          parent_logical_path: entry.parent_logical_path,
          occurrence_key: entry.occurrence_key,
          ...decision,
        }
      );
      setEditStaging(updated);
    } catch (reason) {
      setEditStagingError(
        reason instanceof Error ? reason.message : 'Unable to save identity decision.'
      );
    } finally {
      setEditBusy(false);
    }
  };

  const stageResolvedEditAssembly = async () => {
    if (!editStaging || !editAssemblyAnalysis || !editAnalysisIsCurrent) return;
    if (workerAnalysisFiles.length === 0) {
      setEditStagingError(
        'Select the assembly package again so its analyzed files can be uploaded.'
      );
      return;
    }
    setEditBusy(true);
    setEditStagingError('');
    try {
      let staging = await api<StagingResult>(`/staging/${editStaging.id}`);
      const sources = new Map(
        workerAnalysisFiles.map((file) => [
          (file.webkitRelativePath || file.name).replace(/\\/g, '/').toLowerCase(),
          file,
        ])
      );
      for (const expected of editAssemblyAnalysis.files) {
        const source = sources.get(expected.logical_path.toLowerCase());
        if (!source) throw new Error(`The selected package is missing ${expected.logical_path}.`);
        let staged = staging.files.find(
          (file) => file.logical_path.toLowerCase() === expected.logical_path.toLowerCase()
        );
        if (!staged) {
          staging = await api<StagingResult>(`/staging/${staging.id}/files`, 'POST', {
            expected_snapshot_version: staging.snapshot_version,
            logical_path: expected.logical_path,
            filename: expected.filename,
            byte_size: expected.byte_size,
            sha256: expected.sha256,
            mime_type: source.type || 'application/octet-stream',
          });
          staged = staging.files.find(
            (file) => file.logical_path.toLowerCase() === expected.logical_path.toLowerCase()
          );
        }
        if (!staged) throw new Error(`Unable to stage ${expected.logical_path}.`);
        if (staged.sha256 !== expected.sha256 || staged.byte_size !== expected.byte_size) {
          throw new Error(`${expected.logical_path} differs from the analyzed package.`);
        }
        if (staged.state !== 'VERIFIED') {
          const upload = await fetch(serverApiUrl(staged.upload_url), {
            method: 'PUT',
            headers: {
              ...authorizationHeader(),
              'Content-Type': source.type || 'application/octet-stream',
            },
            body: source,
          });
          if (!upload.ok)
            throw new Error(`Upload failed for ${expected.logical_path} (${upload.status}).`);
          staging = (await upload.json()) as StagingResult;
        }
      }
      const updated = await api<StagingResult>(
        `/staging/${staging.id}/assembly-reconciliation/stage`,
        'POST',
        { expected_snapshot_version: staging.snapshot_version }
      );
      setEditStaging(updated);
      setStagingSnapshotVersion(updated.snapshot_version);
      setEditMessage('The reconciled assembly package and BOM are staged for validation.');
    } catch (reason) {
      setEditStagingError(
        reason instanceof Error ? reason.message : 'Unable to stage the assembly package.'
      );
    } finally {
      setEditBusy(false);
    }
  };

  const prepareModifiedEditChildren = createPrepareModifiedEditChildren({
    editStaging,
    modifiedEditReferences,
    editAssemblyAnalysis,
    editAnalysisIsCurrent,
    setEditStagingError,
    setEditBusy,
    editOccurrences,
    workerAnalysisFiles,
    childEditSessions,
    editReconciliation,
    setChildEditSessions,
    setEditOccurrences,
    stageCadFile,
    dependencyReport,
    setEditMessage,
  });

  const openCadFileWithLocalAgent = async () => {
    const agentBase = 'http://127.0.0.1:47832';
    setCadSelectionBusy(true);
    setCadSelectionStatus('Connecting to local CAD agent…');
    setFileSelectionError('');
    setDependencyReport(null);
    try {
      const formats = await api<
        Array<{
          id: string;
          cadSystem: string;
          workerName: string;
          workerVersion: string;
          sourceExtensions: string[];
        }>
      >('/workers/external-formats');
      const requestSelection = () =>
        fetch(`${agentBase}/api/v1/selections`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(formats),
        });
      const checkHealth = async (): Promise<'ready' | 'forbidden' | 'unavailable'> => {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 1500);
        try {
          const health = await fetch(`${agentBase}/health`, {
            cache: 'no-store',
            signal: controller.signal,
          });
          if (health.ok) return 'ready';
          return health.status === 403 ? 'forbidden' : 'unavailable';
        } catch {
          return 'unavailable';
        } finally {
          window.clearTimeout(timeout);
        }
      };
      let health = await checkHealth();
      if (health === 'forbidden') {
        throw new Error(
          `The Local CAD Agent is running but does not allow ${window.location.origin}. Reinstall it with -AllowedOrigins '${window.location.origin}', then try again.`
        );
      }
      if (health !== 'ready') {
        setCadSelectionStatus('Starting local CAD agent…');
        const launcher = document.createElement('iframe');
        launcher.hidden = true;
        launcher.src = 'modelryx-cad://start';
        document.body.appendChild(launcher);
        try {
          for (let attempt = 0; attempt < 20; attempt++) {
            await new Promise((resolve) => window.setTimeout(resolve, 400));
            health = await checkHealth();
            if (health === 'ready') break;
          }
          if (health === 'forbidden') {
            throw new Error(
              `The Local CAD Agent is running but does not allow ${window.location.origin}. Reinstall it with -AllowedOrigins '${window.location.origin}', then try again.`
            );
          }
          if (health !== 'ready') {
            throw new Error(
              'Modelryx Local CAD Agent is not running. Install or start it, then try again.'
            );
          }
        } finally {
          launcher.remove();
        }
      }
      setCadSelectionStatus('Select one CAD file in the desktop dialog…');
      let response: Response;
      try {
        response = await requestSelection();
      } catch {
        throw new Error('The Local CAD Agent stopped responding. Restart it and try again.');
      }
      if (response.status === 404) {
        throw new Error(
          'The installed Local CAD Agent is outdated or incorrectly configured. Reinstall the current agent and try again.'
        );
      }
      type LocalCadResult = {
        cancelled?: boolean;
        error?: string;
        status?: 'analyzing' | 'complete';
        stage?: string;
        message?: string;
        updated_at?: string;
        status_url?: string;
        report?: CadDependencyReport;
        package_url?: string;
        worker_id?: string | null;
        cad_system?: string | null;
      };
      let result = (await response.json().catch(() => ({}))) as LocalCadResult;
      if (!response.ok)
        throw new Error(result.error || `Local CAD selection failed (${response.status}).`);
      if (result.cancelled) return;
      if (response.status === 202) {
        if (!result.status_url)
          throw new Error('The local CAD agent returned no analysis status address.');
        const statusUrl = new URL(result.status_url, agentBase);
        if (statusUrl.origin !== agentBase)
          throw new Error('The Local CAD Agent returned an unsafe status address.');
        setCadSelectionStatus('Analyzing references with the installed CAD system…');
        // Large legacy assemblies can spend several minutes inside a single native CAD
        // open/resolve call. Keep this slightly longer than the agent's bounded 15-minute limit.
        for (let attempt = 0; attempt < 1860; attempt++) {
          await new Promise((resolve) => window.setTimeout(resolve, 500));
          const pollController = new AbortController();
          const pollTimeout = window.setTimeout(() => pollController.abort(), 5000);
          try {
            response = await fetch(statusUrl, { cache: 'no-store', signal: pollController.signal });
          } catch {
            throw new Error(
              'The Local CAD Agent stopped responding during analysis. Restart it and try again.'
            );
          } finally {
            window.clearTimeout(pollTimeout);
          }
          result = (await response.json().catch(() => ({}))) as LocalCadResult;
          if (response.status === 202) {
            if (result.message) setCadSelectionStatus(result.message);
            continue;
          }
          if (!response.ok)
            throw new Error(result.error || `Local CAD analysis failed (${response.status}).`);
          break;
        }
        if (result.status !== 'complete') throw new Error('Local CAD analysis timed out.');
      }
      if (!result.report || !result.package_url)
        throw new Error('The local CAD agent returned an incomplete result.');
      const selectedWorker = result.worker_id
        ? formats.find(
            (worker) => worker.id === result.worker_id && worker.cadSystem === result.cad_system
          )
        : undefined;
      if (result.worker_id && !selectedWorker)
        throw new Error('The local CAD agent returned an unregistered worker.');
      const report = selectedWorker ? result.report : withoutProcessingSupport(result.report);
      setDependencyReport(report);
      if (report.contract_version !== '1.0') {
        throw new Error(
          'The installed Local CAD Agent uses an unsupported contract. Reinstall the current agent.'
        );
      }
      if (report.validation_issues?.some((issue) => issue.severity === 'ERROR')) {
        setFiles([]);
        setFileSelectionError('The selected CAD document contains unsupported references.');
        return;
      }
      if (report.missing_references.length > 0) {
        setFiles([]);
        setFileSelectionError(
          `${report.missing_references.length} referenced file${report.missing_references.length === 1 ? ' is' : 's are'} missing. Resolve the references in the CAD system and try again.`
        );
        return;
      }
      if (!report.isolated_reopen_validated) {
        setFiles([]);
        throw new Error(
          'The CAD package was not reopened successfully in isolation. No files were uploaded.'
        );
      }
      const packageUrl = new URL(result.package_url, agentBase);
      if (packageUrl.origin !== agentBase) {
        throw new Error('The Local CAD Agent returned an unsafe package address.');
      }
      setCadSelectionStatus('Downloading and verifying the isolated CAD package…');
      const packageResponse = await fetch(packageUrl, { cache: 'no-store' });
      if (!packageResponse.ok)
        throw new Error(`Unable to download the local CAD package (${packageResponse.status}).`);
      const unpacked = unzipSync(new Uint8Array(await packageResponse.arrayBuffer()));
      setCadSelectionStatus('Preparing validated CAD files for upload…');
      const selectedFiles = report.files.map((entry) => {
        const logicalPath = entry.logical_path.replace(/\\/g, '/');
        const bytes = unpacked[logicalPath];
        if (!bytes) throw new Error(`The local CAD package is missing ${logicalPath}.`);
        const file = new File([bytes], logicalPath.split('/').pop() ?? logicalPath, {
          type: 'application/octet-stream',
        });
        Object.defineProperty(file, 'webkitRelativePath', {
          value: logicalPath,
          configurable: true,
        });
        return file;
      });
      await handleFileSelect(
        selectedFiles,
        report.root_logical_path,
        report,
        report.document_type === 'ASSEMBLY',
        selectedWorker?.cadSystem
      );
      setDependencyReport(report);
    } catch (reason) {
      setFiles([]);
      setFileSelectionError(
        reason instanceof Error ? reason.message : 'Local CAD selection failed.'
      );
    } finally {
      setCadSelectionBusy(false);
      setCadSelectionStatus('');
    }
  };

  const runValidation = async () => {
    if (!stagingId) return;
    setValidationBusy(true);
    setValidationError(null);
    setAnalysis(null);
    try {
      const report = await api<ValidationReport>(`/staging/${stagingId}/validate`, 'POST');
      setValidation(report);
      setValidationHistory(
        await api<ValidationReport[]>(`/staging/${stagingId}/validation/history?limit=20`)
      );
    } catch (error) {
      setValidationError((error as Error).message);
    } finally {
      setValidationBusy(false);
    }
  };

  const proposeValidation = async () => {
    if (!stagingId) return;
    setActionBusy(true);
    setValidationError(null);
    setActionExecution(null);
    try {
      setActionProposal(
        await api<AIActionProposal>('/ai/actions/proposals', 'POST', {
          tool_name: 'request_validation',
          arguments: { staging_id: stagingId },
        })
      );
    } catch (error) {
      setValidationError((error as Error).message);
    } finally {
      setActionBusy(false);
    }
  };

  const decideProposal = async (confirm: boolean) => {
    if (!actionProposal || !stagingId) return;
    setActionBusy(true);
    setValidationError(null);
    try {
      if (!confirm) {
        setActionProposal(
          await api<AIActionProposal>(`/ai/actions/proposals/${actionProposal.id}/cancel`, 'POST')
        );
        return;
      }
      const execution = await api<AIActionExecution>(
        `/ai/actions/proposals/${actionProposal.id}/confirm`,
        'POST',
        { confirmed: true, idempotency_key: randomUUID() }
      );
      setActionExecution(execution);
      setActionProposal(await api<AIActionProposal>(`/ai/actions/proposals/${actionProposal.id}`));
      if (execution.status === 'SUCCEEDED') {
        setValidation(await api<ValidationReport>(`/staging/${stagingId}/validation`));
        setValidationHistory(
          await api<ValidationReport[]>(`/staging/${stagingId}/validation/history?limit=20`)
        );
      }
    } catch (error) {
      setValidationError((error as Error).message);
    } finally {
      setActionBusy(false);
    }
  };

  const explainValidation = async () => {
    if (!stagingId || !validation) return;
    setValidationBusy(true);
    setValidationError(null);
    try {
      setAnalysis(await api<ValidationAnalysis>(`/staging/${stagingId}/validation/ai-analysis`));
    } catch (error) {
      setValidationError((error as Error).message);
    } finally {
      setValidationBusy(false);
    }
  };

  const acknowledgeWarning = async (findingId: string) => {
    if (!stagingId || !validation) return;
    setValidationBusy(true);
    setValidationError(null);
    try {
      await api(`/staging/${stagingId}/validation/findings/${findingId}/acknowledge`, 'POST', {});
      setValidation(await api<ValidationReport>(`/staging/${stagingId}/validation`));
    } catch (error) {
      setValidationError((error as Error).message);
    } finally {
      setValidationBusy(false);
    }
  };

  const updateEntry = (index: number, updates: Partial<FileEntry>) => {
    setFiles((prev) =>
      prev.map((entry, i) =>
        i === index
          ? {
              ...entry,
              formError: undefined,
              createdItemId: updates.itemNumber !== undefined ? undefined : entry.createdItemId,
              ...updates,
            }
          : entry
      )
    );
  };

  const chooseDuplicate = async (
    index: number,
    choice: 'USE_EXISTING' | 'CREATE_NEW',
    revisionId?: string
  ) => {
    const entry = files[index];
    const selected =
      entry.duplicates?.find((match) => match.revision_id === revisionId) ?? entry.duplicates?.[0];
    let generatedItemNumber = entry.duplicateChoice === 'USE_EXISTING' ? '' : entry.itemNumber;
    if (choice === 'CREATE_NEW' && !generatedItemNumber) {
      try {
        const generated = await api<GeneratedItemNumber>('/staging/item-numbers/generate', 'POST', {
          item_type: entry.documentType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
          template_id: selectedNumberTemplateId || null,
        });
        generatedItemNumber = generated.item_number;
      } catch (error) {
        updateEntry(index, {
          formError: error instanceof Error ? error.message : 'Unable to allocate an Item number.',
        });
        return;
      }
    }
    updateEntry(index, {
      duplicateChoice: choice,
      isDuplicate: choice === 'USE_EXISTING',
      selectedRevisionId: choice === 'USE_EXISTING' ? selected?.revision_id : undefined,
      ...(choice === 'USE_EXISTING' && selected
        ? {
            itemNumber: selected.item_number,
            itemName: selected.item_name,
            revision: selected.revision_code,
            existingItemNumber: selected.item_number,
            existingItemName: selected.item_name,
            existingRevisionCode: selected.revision_code,
          }
        : { itemNumber: generatedItemNumber }),
    });
    if (selected?.duplicate_check_id) {
      try {
        await api(`/duplicate-check/${selected.duplicate_check_id}/decision`, 'POST', {
          decision: choice,
          candidate_id: choice === 'USE_EXISTING' ? selected.duplicate_candidate_id : null,
          comment: 'Pre-commit local CAD duplicate review',
        });
      } catch (error) {
        updateEntry(index, {
          formError: error instanceof Error ? error.message : 'Unable to save duplicate decision.',
        });
      }
    }
  };

  const reviewIdentities = async (entries: FileEntry[]) => {
    setIdentityBusy(true);
    try {
      const reviews = await checkIdentities(entries);
      let valid = true;
      for (const entry of entries) {
        const review = reviews.find((row) => row.logical_path === entry.logicalPath);
        const index = files.indexOf(entry);
        if (!review) throw new Error('Identity check did not return this file. Please retry.');
        const updating =
          Boolean(targetItemId) && (uploadMode === 'single' || entry.logicalPath === mainFileName);
        const combinedDuplicates = [
          ...(entry.duplicates ?? []),
          ...review.duplicates.filter(
            (candidate) =>
              !(entry.duplicates ?? []).some(
                (existing) => existing.revision_id === candidate.revision_id
              )
          ),
        ];
        const selected = combinedDuplicates.find(
          (match) => match.revision_id === entry.selectedRevisionId
        );
        let error: string | undefined;
        if (entry.duplicateChoice === 'USE_EXISTING' && !selected) {
          error = 'The selected duplicate is no longer available. Review the duplicate choice.';
        } else if (
          review.item_number_exists &&
          !updating &&
          review.existing_item_id !== entry.createdItemId &&
          !(
            entry.duplicateChoice === 'USE_EXISTING' &&
            review.existing_item_id === selected?.item_id
          )
        ) {
          error = `Item number ${entry.itemNumber.trim()} already exists. Enter a different number or choose Use existing Item for an identical file.`;
        } else if (combinedDuplicates.length && !entry.duplicateChoice && !updating) {
          error = 'Potential duplicate found. Choose Use existing Item or Create new Item.';
        } else if (entry.duplicateChoice === 'CREATE_NEW' && !entry.itemNumber.trim()) {
          error = 'Enter a new, unique item number to create another Item from this file.';
        } else if (
          uploadMode === 'folder' &&
          entry.logicalPath === mainFileName &&
          entry.duplicateChoice === 'USE_EXISTING' &&
          selected?.item_type !== 'ASSEMBLY'
        ) {
          error =
            'The main file must use an assembly Item. Choose a different main file or create a new Item.';
        }
        const number = entry.itemNumber.trim().toLowerCase();
        if (
          number &&
          entries.some(
            (other) =>
              other !== entry &&
              other.itemNumber.trim().toLowerCase() === number &&
              !(
                entry.duplicateChoice === 'USE_EXISTING' &&
                other.duplicateChoice === 'USE_EXISTING' &&
                entry.selectedRevisionId === other.selectedRevisionId
              )
          )
        ) {
          error = `Item number ${entry.itemNumber.trim()} is repeated in this upload. Use a different number.`;
        }
        updateEntry(index, { duplicates: combinedDuplicates, formError: error });
        if (error) valid = false;
      }
      return valid;
    } catch (error) {
      for (const entry of entries)
        updateEntry(files.indexOf(entry), { formError: (error as Error).message });
      return false;
    } finally {
      setIdentityBusy(false);
    }
  };

  const removeFile = (index: number) => {
    const removedPath = files[index]?.logicalPath;
    const remaining = files.filter((_, i) => i !== index);
    setFiles(remaining);
    if (removedPath === mainFileName) {
      const validMainFiles = remaining.filter((entry) => entry.documentType === 'ASSEMBLY');
      const candidates =
        validMainFiles.length > 0
          ? validMainFiles
          : remaining.filter((entry) => /\.fcstd$/i.test(entry.file.name));
      const nextMain = candidates.sort(
        (left, right) =>
          logicalPathDepth(left.logicalPath) - logicalPathDepth(right.logicalPath) ||
          left.logicalPath.localeCompare(right.logicalPath)
      )[0];
      setMainFileName(nextMain?.logicalPath ?? '');
    }
    if (removedPath === selectedAssemblyPath) {
      setSelectedAssemblyPath(remaining[0]?.logicalPath ?? '');
    }
  };

  const uploadFile = async (index: number) => {
    const entry = files[index];
    if (!entry.sha256) return;
    await commitNewItem(index);
  };

  const uploadAll = async () => {
    // A completed staging session is immutable. Keep this guard in addition to
    // the completion screen so a delayed or repeated click cannot submit it twice.
    if (committedItemId) return;
    if (uploadMode === 'folder') {
      if (files.some((entry) => entry.externalCadSystem)) {
        const root = files.find((entry) => entry.logicalPath === mainFileName);
        if (dependencyReport && root?.documentType === 'ASSEMBLY' && !targetItemId) {
          await uploadAssembly();
        } else {
          await uploadExternalAssembly();
        }
        return;
      }
      await uploadAssembly();
      return;
    }
    for (let i = 0; i < files.length; i++) {
      if (files[i].status === 'ready') {
        await uploadFile(i);
      }
    }
  };

  const uploadExternalAssembly = async () => {
    const main = files.find((entry) => entry.logicalPath === mainFileName);
    if (!main || !stagingId || files.some((entry) => entry.status !== 'ready')) return;
    const index = files.indexOf(main);
    if (!main.itemNumber.trim() || !main.itemName.trim() || !main.revision.trim()) {
      updateEntry(index, { formError: 'Item number, name and revision are required.' });
      return;
    }
    if (!(await reviewIdentities([main]))) return;
    if (main.duplicateChoice === 'USE_EXISTING') {
      updateEntry(index, { formError: 'Select Create new Item to upload this assembly package.' });
      return;
    }
    setFiles((previous) =>
      previous.map((entry) => ({ ...entry, status: 'uploading', uploadProgress: 0 }))
    );
    try {
      let staging = await api<StagingResult>(`/staging/${stagingId}`);
      const staged = (entry: FileEntry) => {
        const row = staging.files.find((file) => file.logical_path === entry.logicalPath);
        if (!row) throw new Error(`${entry.file.name} is missing from staging.`);
        return row;
      };
      const relatedDrawings = files.filter((entry) => entry !== main && isDrawing(entry));
      staging = await api<StagingResult>(`/staging/${stagingId}/proposal`, 'PUT', {
        expected_snapshot_version: staging.snapshot_version,
        operation: targetItemId ? 'CREATE_REVISION' : 'CREATE_ITEM',
        ...(targetItemId && targetRevisionId
          ? { destination: { item_id: targetItemId, revision_id: targetRevisionId } }
          : {}),
        item: {
          item_number: main.itemNumber.trim(),
          name: main.itemName.trim(),
          item_type: main.documentType === 'ASSEMBLY' ? 'ASSEMBLY' : 'PART',
          description: main.description.trim() || null,
          category: main.category.trim() || null,
        },
        revision: {
          revision_code: main.revision.trim(),
          cad_system: main.externalCadSystem,
          native_format: main.file.name.split('.').pop()?.toUpperCase(),
        },
        artifacts: [
          {
            client_artifact_id: randomUUID(),
            artifact_type:
              main.documentType === 'ASSEMBLY'
                ? 'CAD_ASSEMBLY'
                : main.documentType === 'DRAWING'
                  ? 'DRAWING'
                  : 'CAD_MODEL',
            name: main.itemName.trim(),
            classification: 'AUTHORITATIVE',
            relation_type: 'SPECIFICATION',
            is_primary: true,
            files: files
              .filter((entry) => !relatedDrawings.includes(entry))
              .map((entry) => ({
                staged_file_id: staged(entry).id,
                reference_name: entry.logicalPath,
                role: entry === main ? 'PRIMARY' : 'SECONDARY',
              })),
          },
          ...relatedDrawings.map((drawing) => ({
            client_artifact_id: randomUUID(),
            artifact_type: 'DRAWING',
            name: drawing.file.name.replace(/\.[^.]+$/, ''),
            classification: 'AUTHORITATIVE',
            relation_type: 'DRAWING',
            is_primary: false,
            files: [
              {
                staged_file_id: staged(drawing).id,
                reference_name: drawing.logicalPath,
                role: 'PRIMARY',
              },
            ],
          })),
        ],
        identity_decisions: [{ staged_file_id: staged(main).id, decision: 'CREATE_NEW' }],
        bom: [],
      });
      setStagingSnapshotVersion(staging.snapshot_version);
      for (const entry of files) {
        const row = staged(entry);
        if (row.state === 'VERIFIED') continue;
        const response = await fetch(serverApiUrl(row.upload_url), {
          method: 'PUT',
          headers: { ...authorizationHeader(), 'Content-Type': 'application/octet-stream' },
          body: entry.file,
        });
        if (!response.ok)
          throw new Error(`Upload failed for ${entry.logicalPath} (${response.status}).`);
        staging = (await response.json()) as StagingResult;
      }
      const workflow = await api<StagingWorkflowResult>(`/staging/${stagingId}/submit`, 'POST');
      setCommitMessage(
        'Assembly upload complete. Validation and publication are running in the background.'
      );
      setFiles((previous) =>
        previous.map((entry) => ({
          ...entry,
          status: 'complete',
          uploadProgress: workflow.progress_percent,
        }))
      );
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Unable to upload the assembly.';
      setFiles((previous) =>
        previous.map((entry) => ({
          ...entry,
          status: 'ready',
          formError: entry.logicalPath === mainFileName ? message : undefined,
        }))
      );
    }
  };

  const commitNewItem = async (index: number) => {
    const entry = files[index];
    const profile = {
      ...cadProfile(entry.file.name, entry.documentType),
      ...(entry.externalCadSystem ? { cadSystem: entry.externalCadSystem } : {}),
    };
    if (!entry.sha256 || !stagingId || stagingSnapshotVersion === null) return;
    if (!(await reviewIdentities([entry]))) return;
    if (entry.duplicateChoice === 'USE_EXISTING') {
      const selected = entry.duplicates?.find(
        (match) => match.revision_id === entry.selectedRevisionId
      );
      if (!selected) {
        updateEntry(index, {
          status: 'ready',
          formError: 'The selected duplicate is no longer available. Review the duplicate choice.',
        });
        return;
      }
      if (selected.duplicate_check_id) await api(`/staging/${stagingId}/reuse`, 'POST');
      updateEntry(index, {
        status: 'complete',
        uploadProgress: 100,
        createdItemId: selected.item_id,
      });
      setCommittedItemId(selected.item_id);
      setCommitMessage(`Using existing Item ${selected.item_number}. No CAD file was uploaded.`);
      return;
    }
    const missing = [
      !entry.itemNumber.trim() ? 'Item number is not specified.' : '',
      !entry.itemName.trim() ? 'Item name is not specified.' : '',
      !entry.revision.trim() ? 'Revision is not specified.' : '',
    ].filter(Boolean);
    if (missing.length) {
      updateEntry(index, { status: 'ready', formError: missing.join(' ') });
      return;
    }

    updateEntry(index, {
      status: 'uploading',
      uploadProgress: 0,
      formError: undefined,
      errorMessage: undefined,
    });
    try {
      let staging = await api<StagingResult>(`/staging/${stagingId}`);
      const stagedFile = staging.files.find((row) => row.logical_path === entry.logicalPath);
      if (!stagedFile)
        throw new Error('The selected file is missing from staging. Choose it again.');
      staging = await api<StagingResult>(`/staging/${stagingId}/proposal`, 'PUT', {
        expected_snapshot_version: staging.snapshot_version,
        operation: targetItemId ? 'CREATE_REVISION' : 'CREATE_ITEM',
        ...(targetItemId && targetRevisionId
          ? {
              destination: {
                item_id: targetItemId,
                revision_id: targetRevisionId,
              },
            }
          : {}),
        item: {
          item_number: entry.itemNumber.trim(),
          name: entry.itemName.trim(),
          item_type: profile.itemType,
          description: entry.description.trim() || null,
          category: entry.category.trim() || null,
        },
        revision: {
          revision_code: entry.revision.trim(),
          cad_system: profile.cadSystem,
          native_format: profile.nativeFormat,
        },
        artifacts: [
          {
            client_artifact_id: randomUUID(),
            artifact_type: profile.artifactType,
            name: entry.itemName.trim(),
            classification: 'AUTHORITATIVE',
            relation_type: 'SPECIFICATION',
            is_primary: true,
            files: [
              {
                staged_file_id: stagedFile.id,
                reference_name: 'MODEL',
                role: 'PRIMARY',
              },
            ],
          },
        ],
        identity_decisions: [
          {
            staged_file_id: stagedFile.id,
            decision: 'CREATE_NEW',
          },
        ],
        bom: [],
      });
      setStagingSnapshotVersion(staging.snapshot_version);
      updateEntry(index, { uploadProgress: 10 });

      const currentStagedFile = staging.files.find((row) => row.logical_path === entry.logicalPath);
      if (!currentStagedFile) throw new Error('The selected file is missing from staging.');
      if (currentStagedFile.state !== 'VERIFIED') {
        const response = await fetch(serverApiUrl(currentStagedFile.upload_url), {
          method: 'PUT',
          headers: {
            ...authorizationHeader(),
            'Content-Type': 'application/octet-stream',
          },
          body: entry.file,
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(
            typeof body.detail === 'string'
              ? body.detail
              : `Staging upload failed (${response.status})`
          );
        }
        staging = (await response.json()) as StagingResult;
      }
      updateEntry(index, { uploadProgress: 60 });

      const workflow = await api<StagingWorkflowResult>(`/staging/${stagingId}/submit`, 'POST');
      setCommitMessage(
        'Upload complete. Validation and publication are running in the background.'
      );
      updateEntry(index, {
        status: 'complete',
        uploadProgress: workflow.progress_percent,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to commit the Item';
      updateEntry(index, {
        status: 'ready',
        formError: message,
        uploadProgress: 0,
      });
    }
  };

  const { uploadAssembly } = createAssemblyUploadActions({
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
    targetItemId,
    targetRevisionId,
  });

  const readyCount = files.filter((f) => f.status === 'ready').length;
  const duplicateCount = files.filter((f) => f.duplicates?.length).length;
  const completeCount = files.filter((f) => f.status === 'complete').length;
  const assemblyMainCandidates = files
    .filter((entry) =>
      entry.externalCadSystem
        ? entry.logicalPath === mainFileName
        : /\.(fcstd|step|stp)$/i.test(entry.file.name)
    )
    .sort(
      (left, right) =>
        Number(right.logicalPath === mainFileName) - Number(left.logicalPath === mainFileName) ||
        logicalPathDepth(left.logicalPath) - logicalPathDepth(right.logicalPath) ||
        left.logicalPath.localeCompare(right.logicalPath)
    );

  const renderEntryStatus = (entry: FileEntry) => (
    <div className="status-cell">
      {entry.status === 'hashing' && (
        <span className="status-badge hashing">Calculating hash…</span>
      )}
      {entry.status === 'checking' && <span className="status-badge checking">Checking…</span>}
      {entry.status === 'duplicate' && <span className="status-badge duplicate">Duplicate</span>}
      {entry.status === 'ready' && !entry.formError && (
        <span className="status-badge ready">Ready</span>
      )}
      {entry.formError && (
        <span className="status-badge error" role="alert">
          {entry.formError}
        </span>
      )}
      {entry.status === 'uploading' && (
        <span className="status-badge uploading">Uploading {entry.uploadProgress}%</span>
      )}
      {entry.status === 'complete' && (
        <span className="status-badge complete">
          {entry.isDuplicate ? 'Using existing Item' : 'Complete'}
        </span>
      )}
      {entry.status === 'error' && <span className="status-badge error">{entry.errorMessage}</span>}
    </div>
  );

  const renderDuplicateChoice = (entry: FileEntry, index: number) =>
    Boolean(entry.duplicates?.length) &&
    !(targetItemId && (uploadMode === 'single' || entry.logicalPath === mainFileName)) && (
      <section className="duplicate-choice checkin-property-span">
        <strong>Potential duplicate found</strong>
        <p>Choose an existing Item or explicitly create a new Item with a unique number.</p>
        <select
          aria-label={`Duplicate choice for ${entry.file.name}`}
          disabled={entry.status !== 'ready'}
          value={
            entry.duplicateChoice === 'CREATE_NEW' ? 'CREATE_NEW' : (entry.selectedRevisionId ?? '')
          }
          onChange={(event) =>
            void chooseDuplicate(
              index,
              event.target.value === 'CREATE_NEW' ? 'CREATE_NEW' : 'USE_EXISTING',
              event.target.value
            )
          }
        >
          <option value="" disabled>
            Choose how to continue
          </option>
          {entry.duplicates?.map((match) => (
            <option key={match.revision_id} value={match.revision_id}>
              Use existing Item: {match.item_number} · Rev {match.revision_code}
              {match.classification ? ` · ${match.classification.replaceAll('_', ' ')}` : ''}
            </option>
          ))}
          <option value="CREATE_NEW">Create new Item</option>
        </select>
        {entry.isDuplicate &&
          entry.duplicates?.find((match) => match.revision_id === entry.selectedRevisionId) && (
            <Link
              target="_blank"
              rel="noreferrer"
              to={`/items/${entry.duplicates.find((match) => match.revision_id === entry.selectedRevisionId)!.item_id}`}
            >
              View existing Item
            </Link>
          )}
      </section>
    );

  const renderProperties = (entry: FileEntry, index: number) => {
    if (!createsManagedItem(entry, dependencyReport)) {
      const ownerPath = dependencyReport?.references?.find(
        (reference) =>
          (reference.parent_logical_path === entry.logicalPath &&
            files.some(
              (candidate) =>
                candidate.logicalPath === reference.child_logical_path &&
                createsManagedItem(candidate, dependencyReport)
            )) ||
          (reference.child_logical_path === entry.logicalPath &&
            files.some(
              (candidate) =>
                candidate.logicalPath === reference.parent_logical_path &&
                createsManagedItem(candidate, dependencyReport)
            ))
      );
      const resolvedOwnerPath =
        ownerPath?.parent_logical_path === entry.logicalPath
          ? ownerPath.child_logical_path
          : ownerPath?.parent_logical_path;
      return (
        <fieldset className="checkin-properties" disabled={identityBusy}>
          <div className="checkin-properties-heading">
            <div>
              <span className="checkin-eyebrow">
                {isDrawing(entry) ? 'Related drawing' : 'Supporting CAD file'}
              </span>
              <h2>{entry.file.name}</h2>
              <p title={entry.logicalPath}>
                {entry.logicalPath} · {(entry.file.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
            {renderEntryStatus(entry)}
          </div>
          <p className="muted">
            {isDrawing(entry)
              ? `This drawing will be attached to ${resolvedOwnerPath ?? 'its referenced model'}; it will not create a separate Item.`
              : 'This file supports CAD processing and will not create a separate Item.'}
          </p>
        </fieldset>
      );
    }
    return (
      <fieldset className="checkin-properties" disabled={identityBusy}>
        <div className="checkin-properties-heading">
          <div>
            <span className="checkin-eyebrow">Selected file</span>
            <h2>{entry.file.name}</h2>
            <p title={entry.logicalPath}>
              {entry.logicalPath} · {(entry.file.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
          {renderEntryStatus(entry)}
        </div>
        <div className="checkin-property-grid">
          {renderDuplicateChoice(entry, index)}
          <label>
            Item Number
            {entry.isDuplicate ? (
              <span className="checkin-readonly-value">{entry.existingItemNumber}</span>
            ) : (
              <input
                type="text"
                value={entry.itemNumber}
                onChange={(event) => updateEntry(index, { itemNumber: event.target.value })}
                disabled={entry.status !== 'ready'}
                placeholder="e.g., PART-001"
                required
              />
            )}
          </label>
          <label>
            Item Name
            {entry.isDuplicate ? (
              <span className="checkin-readonly-value">{entry.existingItemName}</span>
            ) : (
              <input
                type="text"
                value={entry.itemName}
                onChange={(event) => updateEntry(index, { itemName: event.target.value })}
                disabled={entry.status !== 'ready'}
                required
              />
            )}
          </label>
          <label className="checkin-property-span">
            Description
            {entry.isDuplicate ? (
              <span className="checkin-readonly-value muted">Existing Item</span>
            ) : (
              <textarea
                value={entry.description}
                onChange={(event) => updateEntry(index, { description: event.target.value })}
                disabled={entry.status !== 'ready' || Boolean(targetItemId)}
                placeholder="Optional item description"
                maxLength={10000}
                rows={5}
              />
            )}
          </label>
          <label>
            Revision
            {entry.isDuplicate ? (
              <span className="checkin-readonly-value">{entry.existingRevisionCode}</span>
            ) : (
              <input
                type="text"
                value={entry.revision}
                onChange={(event) => updateEntry(index, { revision: event.target.value })}
                disabled={entry.status !== 'ready' || Boolean(targetItemId)}
                required
              />
            )}
          </label>
          <label>
            Category
            {entry.isDuplicate ? (
              <span className="checkin-readonly-value muted">—</span>
            ) : (
              <input
                type="text"
                value={entry.category}
                onChange={(event) => updateEntry(index, { category: event.target.value })}
                disabled={entry.status !== 'ready'}
                placeholder="Optional"
              />
            )}
          </label>
        </div>
      </fieldset>
    );
  };

  return {
    editMode,
    embedded,
    commitMessage,
    committedItemId,
    committedRevisionId,
    commitProcessingJobs,
    commitProcessingLoaded,
    commitProcessingError,
    targetItemId,
    targetItemNumber,
    targetItemName,
    targetRevision,
    files,
    dependencyReport,
    workerAnalysisFiles,
    cadSelectionBusy,
    editStaging,
    setEditStaging,
    editStagingError,
    editBusy,
    editMessage,
    editWhereUsed,
    editContentMenu,
    setEditContentMenu,
    editOccurrences,
    collapsedEditNodes,
    editReconciliationFilter,
    setEditReconciliationFilter,
    showEditReview,
    setShowEditReview,
    childEditSessions,
    editOccurrenceMenu,
    setEditOccurrenceMenu,
    toggleEditNode,
    visibleEditOccurrences,
    editAssemblyAnalysis,
    editReconciliation,
    editAnalysisRows,
    editAnalysisIsCurrent,
    unresolvedEditIdentities,
    modifiedEditReferences,
    filteredEditReconciliation,
    editReconciliationCounts,
    unpreparedModifiedReferences,
    editBlockingCount,
    editCreateNewCount,
    editReuseCount,
    editAssemblyReviewBlocked,
    checkoutChild,
    cancelChildCheckout,
    addEditFile,
    checkinChildFile,
    downloadEditFile,
    submitEdit,
    retryCommitProcessing,
    cancelEdit,
    importEditCadPackage,
    analyzeExternalEditFolder,
    applySafeEditReconciliation,
    resolveEditAssemblyIdentity,
    stageResolvedEditAssembly,
    prepareModifiedEditChildren,
    targetItemType,
    resumeStageId,
    setFiles,
    numberTemplates,
    selectedNumberTemplateId,
    setSelectedNumberTemplateId,
    identityBusy,
    uploadMode,
    mainFileName,
    setMainFileName,
    selectedAssemblyPath,
    setSelectedAssemblyPath,
    fileSelectionError,
    setFileSelectionError,
    workerWarning,
    cadSelectionStatus,
    stagingId,
    resumeStage,
    resumeStageLoading,
    validation,
    validationHistory,
    analysis,
    validationBusy,
    validationError,
    actionProposal,
    actionExecution,
    actionBusy,
    resumeDisplayName,
    resumeWithFiles,
    openCadFileWithLocalAgent,
    runValidation,
    proposeValidation,
    decideProposal,
    explainValidation,
    acknowledgeWarning,
    removeFile,
    uploadFile,
    uploadAll,
    readyCount,
    duplicateCount,
    completeCount,
    assemblyMainCandidates,
    renderEntryStatus,
    renderProperties,
  };
}

export type CheckinController = ReturnType<typeof useCheckinWorkflowController>;
