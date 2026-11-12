import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, authorizationHeader, serverApiUrl, type Item } from '../../workflow-api';
import { randomUUID } from '../../uuid';
import type { AdditionalFile, AdditionalFileVersion } from './types';

const fileSha256 = async (file: File) =>
  Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())))
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');

const dateTime = (value?: string | null) => (value ? new Date(value).toLocaleString() : '—');

export function AdditionalFilesSection({
  item,
  files,
  canEdit,
  scope,
  revisionId,
  editorPage = false,
  viewTaskId = null,
  onFilesChanged,
}: {
  item: Item;
  files: AdditionalFile[];
  canEdit: boolean;
  scope: 'ITEM' | 'REVISION';
  revisionId: string | null;
  editorPage?: boolean;
  viewTaskId?: string | null;
  onFilesChanged: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [historyFile, setHistoryFile] = useState<AdditionalFile | null>(null);
  const [attachmentMenu, setAttachmentMenu] = useState<{
    file: AdditionalFile;
    x: number;
    y: number;
  } | null>(null);
  const [editingAttachments, setEditingAttachments] = useState(false);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const replacementInputRef = useRef<HTMLInputElement>(null);
  const [replacementFile, setReplacementFile] = useState<AdditionalFile | null>(null);
  type AttachmentDraft = {
    file: File;
    existing?: AdditionalFile;
    optimisticId: string;
    sha256: string;
    uploadRequired: boolean;
    candidates: Array<{
      attachment_id: string;
      attachment_name: string;
      filename: string;
      version_id: string;
      scope: 'ITEM' | 'REVISION';
      revision_id: string | null;
    }>;
  };
  const [attachmentDraft, setAttachmentDraft] = useState<AttachmentDraft | null>(null);
  const [editMetadata, setEditMetadata] = useState<AdditionalFile | null>(null);
  const [hasActiveEdit, setHasActiveEdit] = useState(false);
  const [reviewChanges, setReviewChanges] = useState(false);
  type PendingAttachmentStage = {
    id: string;
    filename: string;
    attachmentName: string;
    attachmentId: string | null;
    byteSize: number;
    action: 'CREATE' | 'NEW_VERSION' | 'REMOVE';
    status: 'UPLOADING' | 'DRAFT' | 'READY' | 'COMPLETED';
  };
  const [pendingStages, setPendingStages] = useState<PendingAttachmentStage[]>([]);
  const editEndpoint =
    scope === 'REVISION' && revisionId
      ? `/items/${item.id}/revisions/${revisionId}/attachment-edit`
      : `/items/${item.id}/attachment-edit`;
  const editorUrl =
    scope === 'REVISION' && revisionId
      ? `/items/${item.id}/revisions/${revisionId}/attachments/edit`
      : `/items/${item.id}/attachments/edit`;
  const stagesUrl = viewTaskId
    ? `/attachment-edit-tasks/${encodeURIComponent(viewTaskId)}/changes`
    : scope === 'REVISION' && revisionId
      ? `/items/${item.id}/additional-files/staging?scope=REVISION&revision_id=${encodeURIComponent(revisionId)}`
      : `/items/${item.id}/additional-files/staging?scope=ITEM`;

  useEffect(() => {
    let active = true;
    api<
      Array<{
        id: string;
        filename: string;
        name: string;
        attachment_id: string | null;
        byte_size: number;
        action: 'CREATE' | 'NEW_VERSION' | 'REMOVE';
        status: 'DRAFT' | 'READY' | 'COMPLETED';
      }>
    >(stagesUrl)
      .then((stages) => {
        if (!active) return;
        const loadedStages: PendingAttachmentStage[] = stages.map((stage) => ({
          id: stage.id,
          filename: stage.filename,
          attachmentName: stage.name,
          attachmentId: stage.attachment_id,
          byteSize: stage.byte_size,
          action: stage.action,
          status: stage.status,
        }));
        setPendingStages((current) => {
          const uploads = current.filter((stage) => stage.status === 'UPLOADING');
          return [
            ...uploads,
            ...loadedStages.filter((stage) => !uploads.some((upload) => upload.id === stage.id)),
          ];
        });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [stagesUrl]);

  useEffect(() => {
    if (viewTaskId) {
      setHasActiveEdit(false);
      return;
    }
    api<{ active: boolean }>(editEndpoint)
      .then((result) => setHasActiveEdit(result.active))
      .catch(() => setHasActiveEdit(false));
  }, [editEndpoint, viewTaskId]);

  useEffect(() => {
    if (!attachmentMenu) return;
    const close = () => setAttachmentMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', closeOnEscape);
    window.addEventListener('blur', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', closeOnEscape);
      window.removeEventListener('blur', close);
    };
  }, [attachmentMenu]);

  const beginEditing = async (): Promise<boolean> => {
    setBusy(true);
    setMessage('');
    try {
      await api(editEndpoint, 'POST');
      setEditingAttachments(true);
      setHasActiveEdit(true);
      setMessage('Attachment editing checkout acquired.');
      return true;
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to start attachment editing');
      return false;
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (editorPage && canEdit && !viewTaskId && !editingAttachments) void beginEditing();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorPage, canEdit, editEndpoint, viewTaskId]);

  const stageFile = async (file: File, existing?: AdditionalFile) => {
    if (!editingAttachments && !(await beginEditing())) return;
    const optimisticId = `upload-${randomUUID()}`;
    const optimisticStage: PendingAttachmentStage = {
      id: optimisticId,
      filename: file.name,
      attachmentName: existing?.name ?? file.name,
      attachmentId: existing?.id ?? null,
      byteSize: file.size,
      action: existing ? 'NEW_VERSION' : 'CREATE',
      status: 'UPLOADING',
    };
    setPendingStages((current) => [...current, optimisticStage]);
    setBusy(true);
    setMessage(`Checking ${file.name}…`);
    try {
      const sha256 = await fileSha256(file);
      const preflight = await api<{
        classification: string;
        upload_required: boolean;
        candidates: Array<{
          attachment_id: string;
          attachment_name: string;
          filename: string;
          version_id: string;
          scope: 'ITEM' | 'REVISION';
          revision_id: string | null;
        }>;
      }>(`/items/${item.id}/additional-files/preflight`, 'POST', {
        name: existing?.name ?? file.name,
        filename: file.name,
        mime_type: file.type || 'application/octet-stream',
        byte_size: file.size,
        sha256,
        attachment_id: existing?.id ?? null,
        scope,
        revision_id: scope === 'REVISION' ? revisionId : null,
      });
      if (preflight.classification === 'EXACT_CURRENT_VERSION') {
        setPendingStages((current) => current.filter((entry) => entry.id !== optimisticId));
        setMessage(`${file.name} is already the current version.`);
        return;
      }
      setAttachmentDraft({
        file,
        existing,
        optimisticId,
        sha256,
        uploadRequired: preflight.upload_required,
        candidates: preflight.candidates.filter(
          (candidate) => candidate.attachment_id !== existing?.id
        ),
      });
      setMessage('');
    } catch (reason) {
      setPendingStages((current) => current.filter((entry) => entry.id !== optimisticId));
      setMessage(reason instanceof Error ? reason.message : 'Unable to inspect file');
    } finally {
      setBusy(false);
    }
  };

  const confirmAttachmentDraft = async (
    name: string,
    description: string,
    resolution: 'EXISTING' | 'NEW'
  ) => {
    if (!attachmentDraft) return;
    const { file, existing, optimisticId, sha256, uploadRequired, candidates } = attachmentDraft;
    setAttachmentDraft(null);
    if (resolution === 'EXISTING' && candidates.length > 0) {
      setPendingStages((current) => current.filter((entry) => entry.id !== optimisticId));
      setMessage(
        `Using existing attachment ${candidates[0].attachment_name}; no duplicate was created.`
      );
      return;
    }
    setBusy(true);
    setMessage(`Staging ${file.name}…`);
    try {
      const stage = await api<{ id: string; upload_url: string | null }>(
        `/items/${item.id}/additional-files/staging`,
        'POST',
        {
          client_staging_id: randomUUID(),
          attachment_id: existing?.id ?? null,
          name: name.trim(),
          attachment_type: existing?.attachment_type ?? 'OTHER',
          description: description.trim() || null,
          filename: file.name,
          mime_type: file.type || 'application/octet-stream',
          byte_size: file.size,
          sha256,
          duplicate_action: uploadRequired ? 'UPLOAD' : 'REUSE_EXISTING',
          duplicate_confirmed: candidates.length > 0,
          duplicate_resolution: resolution,
          scope,
          revision_id: scope === 'REVISION' ? revisionId : null,
        }
      );

      if (stage.upload_url) {
        const response = await fetch(serverApiUrl(stage.upload_url), {
          method: 'PUT',
          headers: {
            ...authorizationHeader(),
            'Content-Type': file.type || 'application/octet-stream',
          },
          body: file,
        });
        if (!response.ok) throw new Error(`Upload failed (${response.status}).`);
      }

      setPendingStages((current) => {
        const completed: PendingAttachmentStage = {
          id: stage.id,
          filename: file.name,
          attachmentName: name.trim(),
          attachmentId: existing?.id ?? null,
          byteSize: file.size,
          action: existing ? 'NEW_VERSION' : 'CREATE',
          status: 'READY',
        };
        return current.some((entry) => entry.id === optimisticId)
          ? current.map((entry) => (entry.id === optimisticId ? completed : entry))
          : [...current.filter((entry) => entry.id !== stage.id), completed];
      });
      setMessage(`${name.trim()} is staged. Review and commit the change.`);
    } catch (reason) {
      setPendingStages((current) => current.filter((entry) => entry.id !== optimisticId));
      setMessage(reason instanceof Error ? reason.message : 'Unable to add file');
    } finally {
      setBusy(false);
    }
  };

  const updateMetadata = async (file: AdditionalFile, name: string, description: string) => {
    setBusy(true);
    try {
      if (file.checkout_status !== 'CHECKED_OUT_BY_ME') {
        await api(`/attachments/${file.id}/checkout`, 'POST');
      }
      await api(`/attachments/${file.id}`, 'PATCH', {
        name: name.trim(),
        description: description.trim() || null,
      });
      if (!editorPage) await api(`/attachments/${file.id}/checkout`, 'DELETE');
      setEditMetadata(null);
      await onFilesChanged();
      setMessage('Attachment details updated.');
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to update attachment');
    } finally {
      setBusy(false);
    }
  };

  const checkout = async (file: AdditionalFile) => {
    setBusy(true);
    setMessage('');
    try {
      await api(`/attachments/${file.id}/checkout`, 'POST');
      await onFilesChanged();
      setMessage(`${file.name} is checked out to you.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to check out attachment');
    } finally {
      setBusy(false);
    }
  };

  const releaseCheckout = async (file: AdditionalFile) => {
    setBusy(true);
    setMessage('');
    try {
      await api(`/attachments/${file.id}/checkout`, 'DELETE');
      await onFilesChanged();
      setMessage(`${file.name} checkout was released.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to release checkout');
    } finally {
      setBusy(false);
    }
  };

  const cancelStage = async (pendingStage: PendingAttachmentStage) => {
    setBusy(true);
    try {
      await api(`/additional-files/staging/${pendingStage.id}`, 'DELETE');
      if (pendingStage.attachmentId) {
        await api(`/attachments/${pendingStage.attachmentId}/checkout`, 'DELETE');
      }
      setPendingStages((current) => current.filter((stage) => stage.id !== pendingStage.id));
      await onFilesChanged();
      setMessage('The staged attachment change was cancelled.');
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to cancel attachment change');
    } finally {
      setBusy(false);
    }
  };

  const commitAllStages = async () => {
    if (pendingStages.length === 0) return;
    setBusy(true);
    try {
      for (const stage of pendingStages) {
        await api(`/additional-files/staging/${stage.id}/commit`, 'POST', {
          commit_id: randomUUID(),
        });
      }
      await api(editEndpoint, 'DELETE');
      setPendingStages([]);
      setReviewChanges(false);
      setEditingAttachments(false);
      setHasActiveEdit(false);
      await onFilesChanged();
      navigate(
        scope === 'REVISION' && revisionId
          ? `/items/${item.id}/revisions/${revisionId}?section=files`
          : `/items/${item.id}?section=attachment`
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to commit attachment changes');
    } finally {
      setBusy(false);
    }
  };

  const cancelEditTask = async () => {
    if (!window.confirm('Cancel this attachment edit task and discard all staged changes?')) return;
    setBusy(true);
    try {
      for (const stage of pendingStages) {
        await api(`/additional-files/staging/${stage.id}`, 'DELETE');
      }
      await api(`${editEndpoint}?cancel=true`, 'DELETE');
      setPendingStages([]);
      setEditingAttachments(false);
      setHasActiveEdit(false);
      navigate(
        scope === 'REVISION' && revisionId
          ? `/items/${item.id}/revisions/${revisionId}?section=files`
          : `/items/${item.id}?section=attachment`
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to cancel attachment task');
    } finally {
      setBusy(false);
    }
  };

  const download = async (version: AdditionalFileVersion) => {
    if (!version.download_url) return;
    try {
      const response = await fetch(serverApiUrl(version.download_url), {
        headers: authorizationHeader(),
      });
      if (!response.ok) throw new Error(`Download failed (${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = version.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Download failed');
    }
  };

  const stageRemoval = async (file: AdditionalFile) => {
    setBusy(true);
    setAttachmentMenu(null);
    try {
      const stage = await api<{
        id: string;
        filename: string;
        name: string;
        attachment_id: string;
        byte_size: number;
        action: 'REMOVE';
        status: 'READY';
      }>(`/attachments/${file.id}/staging-remove`, 'POST', {
        scope,
        revision_id: scope === 'REVISION' ? revisionId : null,
      });
      setPendingStages((current) => [
        ...current.filter((entry) => entry.id !== stage.id),
        {
          id: stage.id,
          filename: stage.filename,
          attachmentName: stage.name,
          attachmentId: stage.attachment_id,
          byteSize: stage.byte_size,
          action: 'REMOVE',
          status: 'READY',
        },
      ]);
      await onFilesChanged();
      setMessage(`${file.name} is staged for removal.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Unable to stage attachment removal');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="item-section">
      <div className="item-section-header">
        <div>
          <h2>Attachment</h2>
          <p className="muted">
            Files that belong to the item and are shared across its revisions.
          </p>
        </div>
        {canEdit && !editorPage && (
          <Link className="primary-button" to={editorUrl}>
            {hasActiveEdit ? 'Continue Editing' : 'Check out & Edit'}
          </Link>
        )}
      </div>

      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}

      <section className="item-panel">
        {(
          viewTaskId ? pendingStages.length === 0 : files.length === 0 && pendingStages.length === 0
        ) ? (
          <p className="muted">
            {viewTaskId
              ? 'This task did not commit any attachment file changes.'
              : 'No attachments have been added to this item.'}
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>File</th>
                  <th>{viewTaskId ? 'Change' : 'Version'}</th>
                  <th>Size</th>
                </tr>
              </thead>
              <tbody>
                {!viewTaskId &&
                  files.map((file) => {
                    const version = file.latest_ready_version;
                    return (
                      <tr
                        key={file.id}
                        className={attachmentMenu?.file.id === file.id ? 'selected' : ''}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setAttachmentMenu({
                            file,
                            x: Math.max(8, Math.min(window.innerWidth - 200, event.clientX)),
                            y: Math.max(8, Math.min(window.innerHeight - 220, event.clientY)),
                          });
                        }}
                      >
                        <td>
                          <div className="attachment-name-cell">
                            {editorPage && !viewTaskId && (
                              <input
                                type="checkbox"
                                aria-label={`Check out ${file.name}`}
                                checked={file.checkout_status === 'CHECKED_OUT_BY_ME'}
                                disabled={
                                  busy ||
                                  file.checkout_status === 'CHECKED_OUT_BY_OTHER' ||
                                  pendingStages.some((stage) => stage.attachmentId === file.id)
                                }
                                onChange={(event) =>
                                  void (event.currentTarget.checked
                                    ? checkout(file)
                                    : releaseCheckout(file))
                                }
                              />
                            )}
                            <strong>{file.name}</strong>
                            {file.checkout_status === 'CHECKED_OUT_BY_OTHER' && (
                              <span className="muted">Checked out</span>
                            )}
                          </div>
                        </td>
                        <td>{version?.filename ?? '—'}</td>
                        <td>{version ? `v${version.version_number}` : '—'}</td>
                        <td>{version ? `${Math.ceil(version.byte_size / 1024)} KB` : '—'}</td>
                      </tr>
                    );
                  })}
                {editorPage &&
                  pendingStages.map((stage) => (
                    <tr key={stage.id} className="attachment-staged-row">
                      <td>
                        <strong>{stage.attachmentName}</strong>
                      </td>
                      <td>{stage.filename}</td>
                      <td>
                        {stage.action === 'REMOVE'
                          ? 'Remove'
                          : stage.action === 'NEW_VERSION'
                            ? 'Check in'
                            : 'New'}
                      </td>
                      <td>{`${Math.ceil(stage.byteSize / 1024)} KB`}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {attachmentMenu &&
        (() => {
          const selected = attachmentMenu.file;
          const version = selected.latest_ready_version;
          const canChange =
            editorPage && editingAttachments && selected.checkout_status === 'CHECKED_OUT_BY_ME';
          return (
            <nav
              className="attachment-row-menu"
              style={{ left: attachmentMenu.x, top: attachmentMenu.y }}
              onMouseDown={(event) => event.stopPropagation()}
              aria-label={`Actions for ${selected.name}`}
            >
              <button
                type="button"
                disabled={!version?.download_url}
                onClick={() => {
                  setAttachmentMenu(null);
                  if (version) void download(version);
                }}
              >
                Download
              </button>
              <button
                type="button"
                onClick={() => {
                  setAttachmentMenu(null);
                  setHistoryFile(selected);
                }}
              >
                History
              </button>
              {canEdit && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setAttachmentMenu(null);
                    setEditMetadata(selected);
                  }}
                >
                  Edit
                </button>
              )}
              {canChange && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setAttachmentMenu(null);
                    setReplacementFile(selected);
                    window.setTimeout(() => replacementInputRef.current?.click(), 0);
                  }}
                >
                  New version
                </button>
              )}
              {canChange && (
                <button
                  type="button"
                  disabled={
                    busy || pendingStages.some((stage) => stage.attachmentId === selected.id)
                  }
                  onClick={() => {
                    void stageRemoval(selected);
                  }}
                >
                  Remove
                </button>
              )}
            </nav>
          );
        })()}
      <input
        ref={replacementInputRef}
        type="file"
        hidden
        onChange={(event) => {
          const selected = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          if (selected && replacementFile) void stageFile(selected, replacementFile);
          setReplacementFile(null);
        }}
      />

      {attachmentDraft && (
        <div className="item-edit-backdrop" role="presentation">
          <section
            className="item-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="attachment-details-title"
          >
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void confirmAttachmentDraft(
                  String(data.get('name')),
                  String(data.get('description') ?? ''),
                  String(data.get('resolution')) as 'EXISTING' | 'NEW'
                );
              }}
            >
              <div>
                <h2 id="attachment-details-title">Attachment details</h2>
                <p className="muted">{attachmentDraft.file.name}</p>
              </div>
              <label>
                Name
                <input
                  name="name"
                  required
                  maxLength={255}
                  defaultValue={attachmentDraft.existing?.name ?? attachmentDraft.file.name}
                />
              </label>
              <label>
                Description
                <textarea
                  name="description"
                  maxLength={10000}
                  defaultValue={attachmentDraft.existing?.description ?? ''}
                />
              </label>
              {attachmentDraft.candidates.length > 0 && (
                <fieldset className="attachment-duplicate-options">
                  <legend>Duplicate content found</legend>
                  <p className="muted">
                    Same SHA-256:{' '}
                    {attachmentDraft.candidates
                      .map(
                        (candidate) =>
                          `${candidate.attachment_name} (${candidate.scope === 'ITEM' ? 'Item' : 'Revision'})`
                      )
                      .join(', ')}
                  </p>
                  <label>
                    <input type="radio" name="resolution" value="EXISTING" defaultChecked />
                    Use existing attachment (do not create a duplicate)
                  </label>
                  <label>
                    <input type="radio" name="resolution" value="NEW" />
                    Create a separate new attachment
                  </label>
                </fieldset>
              )}
              {attachmentDraft.candidates.length === 0 && (
                <input type="hidden" name="resolution" value="NEW" />
              )}
              <div className="item-edit-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => {
                    setPendingStages((current) =>
                      current.filter((stage) => stage.id !== attachmentDraft.optimisticId)
                    );
                    setAttachmentDraft(null);
                  }}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit">
                  OK
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {editMetadata && (
        <div className="item-edit-backdrop" role="presentation">
          <section
            className="item-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-attachment-details-title"
          >
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void updateMetadata(
                  editMetadata,
                  String(data.get('name')),
                  String(data.get('description') ?? '')
                );
              }}
            >
              <h2 id="edit-attachment-details-title">Edit attachment</h2>
              <label>
                Name
                <input name="name" required maxLength={255} defaultValue={editMetadata.name} />
              </label>
              <label>
                Description
                <textarea
                  name="description"
                  maxLength={10000}
                  defaultValue={editMetadata.description ?? ''}
                />
              </label>
              <div className="item-edit-actions">
                <button
                  className="secondary-button"
                  type="button"
                  disabled={busy}
                  onClick={() => setEditMetadata(null)}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={busy}>
                  Save
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {canEdit && editorPage && (
        <div className="item-edit-actions attachment-editor-actions">
          <button
            className="primary-button"
            type="button"
            disabled={busy || Boolean(viewTaskId)}
            onClick={() => attachmentInputRef.current?.click()}
          >
            Add Attachment
          </button>
          <input
            ref={attachmentInputRef}
            type="file"
            hidden
            multiple
            onChange={(event) => {
              const selected = Array.from(event.target.files ?? []);
              event.currentTarget.value = '';
              if (selected.length > 0)
                void (async () => {
                  for (const file of selected) await stageFile(file);
                })();
            }}
          />
          <button
            className="primary-button"
            type="button"
            disabled={
              busy || Boolean(viewTaskId) || !editingAttachments || pendingStages.length === 0
            }
            onClick={() => setReviewChanges(true)}
          >
            Review Changes
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || (!viewTaskId && !editingAttachments)}
            onClick={() => (viewTaskId ? navigate('/workspace') : void cancelEditTask())}
          >
            Cancel
          </button>
        </div>
      )}

      {editorPage && reviewChanges && (
        <section className="item-panel attachment-review">
          <div>
            <h3>Review Changes</h3>
            <p className="muted">
              {pendingStages.length} attachment change{pendingStages.length === 1 ? '' : 's'} ready
              to commit.
            </p>
            <ul>
              {pendingStages.map((stage) => (
                <li key={stage.id}>
                  <strong>{stage.attachmentName}</strong> — {stage.filename}
                  {stage.action === 'REMOVE' ? ' — Remove' : ''}
                </li>
              ))}
            </ul>
          </div>
          <div className="row-actions">
            <button
              className="secondary-button"
              type="button"
              disabled={busy}
              onClick={() => setReviewChanges(false)}
            >
              Back
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={busy || pendingStages.some((stage) => stage.status !== 'READY')}
              onClick={() => void commitAllStages()}
            >
              {busy ? 'Committing…' : 'Commit all changes'}
            </button>
            {pendingStages.length === 1 && (
              <button
                className="secondary-button"
                type="button"
                disabled={busy}
                onClick={() => void cancelStage(pendingStages[0])}
              >
                Discard change
              </button>
            )}
          </div>
        </section>
      )}

      {historyFile && (
        <div className="item-edit-backdrop" role="presentation">
          <section
            className="item-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="additional-file-history-title"
          >
            <div>
              <h2 id="additional-file-history-title">{historyFile.name}</h2>
              <p className="muted">Version history</p>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>File</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {historyFile.versions.map((version) => (
                    <tr key={version.id}>
                      <td>v{version.version_number}</td>
                      <td>{version.filename}</td>
                      <td>{dateTime(version.ready_at ?? version.created_at)}</td>
                      <td>
                        {version.download_url && (
                          <button type="button" onClick={() => void download(version)}>
                            Download
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="item-edit-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setHistoryFile(null)}
              >
                Close
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
