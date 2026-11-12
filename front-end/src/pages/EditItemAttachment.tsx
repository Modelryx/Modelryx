import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Item } from "../workflow-api";
import { AdditionalFilesSection } from "./item/AdditionalFilesSection";
import type { AdditionalFile } from "./item/types";
import "./Item.css";

type Revision = {
  id: string;
  revision_code: string;
};

export function EditItemAttachmentPage() {
  const { itemId, revisionId } = useParams();
  const [searchParams] = useSearchParams();
  const taskId = searchParams.get("taskId");
  const [item, setItem] = useState<Item | null>(null);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [files, setFiles] = useState<AdditionalFile[]>([]);
  const [error, setError] = useState("");

  const loadFiles = useCallback(async () => {
    if (!itemId) return;
    const url = revisionId
      ? `/items/${itemId}/revisions/${revisionId}/attachments`
      : `/items/${itemId}/attachments`;
    setFiles(await api<AdditionalFile[]>(url));
  }, [itemId, revisionId]);

  useEffect(() => {
    if (!itemId) return;
    const filesUrl = revisionId
      ? `/items/${itemId}/revisions/${revisionId}/attachments`
      : `/items/${itemId}/attachments`;
    Promise.all([
      api<Item>(`/items/${itemId}`),
      api<Revision[]>(`/items/${itemId}/revisions`),
      api<AdditionalFile[]>(filesUrl),
    ]).then(([loadedItem, revisions, loadedFiles]) => {
      setItem(loadedItem);
      setRevision(revisionId ? revisions.find((entry) => entry.id === revisionId) ?? null : null);
      setFiles(loadedFiles);
    }).catch((reason) => setError(
      reason instanceof Error ? reason.message : "Unable to load attachment editor",
    ));
  }, [itemId, revisionId]);

  if (error) return <section className="page"><p className="error">{error}</p></section>;
  if (!item || (revisionId && !revision)) {
    return <section className="page"><p>Loading attachment editor…</p></section>;
  }

  return (
    <section className="page item-page">
      <div className="item-breadcrumb">
        <Link to={`/items/${item.id}`}>{item.item_number}</Link>
        {revision && <><span>/</span>
          <Link to={`/items/${item.id}/revisions/${revision.id}`}>Revision {revision.revision_code}</Link>
        </>}
        <span>/</span>
        <span>{taskId ? "View Attachment Changes" : "Edit Attachment"}</span>
      </div>
      <header className="item-heading">
        <div>
          <p className="eyebrow">{taskId ? "VIEW COMPLETED ATTACHMENT TASK"
            : revision ? "EDIT REVISION ATTACHMENT" : "EDIT ITEM ATTACHMENT"}</p>
          <h1>{item.name}{revision ? ` · Revision ${revision.revision_code}` : ""}</h1>
          <p className="page-subtitle">
            {taskId ? "Review the files changed by this completed workspace task."
              : revision ? "Manage files attached only to this revision."
                : "Manage files attached to the item across revisions."}
          </p>
        </div>
      </header>
      <AdditionalFilesSection
        item={item}
        files={files}
        canEdit
        scope={revision ? "REVISION" : "ITEM"}
        revisionId={revisionId ?? null}
        editorPage
        viewTaskId={taskId}
        onFilesChanged={loadFiles}
      />
    </section>
  );
}
