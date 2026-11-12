import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type AssistantAction,
  type AssistantMessageResponse,
  type AssistantReference,
  type AssistantViewerAction,
  type AssistantViewerCommand,
} from "../api";
import { Button } from "../components/Button";
import "./RevisionAssistant.css";

type Exchange = { id: string; question: string; response: AssistantMessageResponse };

function referencePath(response: AssistantMessageResponse, reference: AssistantReference): string | null {
  const exact = response.actions.find((action): action is Extract<AssistantAction, { type: "OPEN_REVISION" }> =>
    action.type === "OPEN_REVISION" && action.item_id === reference.item_id
      && action.revision_id === reference.revision_id);
  if (exact?.revision_id) return `/items/${encodeURIComponent(exact.item_id)}/revisions/${encodeURIComponent(exact.revision_id)}`;
  const item = response.actions.find((action): action is Extract<AssistantAction, { type: "OPEN_ITEM" }> =>
    action.type === "OPEN_ITEM" && action.item_id === reference.item_id);
  return item ? `/items/${encodeURIComponent(item.item_id)}` : null;
}

export function RevisionAssistant({ itemId, revisionId, itemNumber, isAssembly, selectedOccurrenceId,
  historical, onViewerAction }: {
  itemId: string;
  revisionId: string;
  itemNumber: string;
  isAssembly: boolean;
  selectedOccurrenceId?: string;
  historical: boolean;
  onViewerAction: (command: AssistantViewerCommand) => void;
}) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<string>();
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlightRef = useRef(false);
  const activeRef = useRef(true);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { activeRef.current = true; return () => { activeRef.current = false; }; }, []);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
  const close = () => { setOpen(false); window.requestAnimationFrame(() => triggerRef.current?.focus()); };
  const suggestions = [
    "Summarize this item",
    "What material is this?",
    isAssembly ? "How many holes are in this assembly?" : "Does this part have holes?",
    "Show revision history",
    "What changed from the previous revision?",
    "Where is this used?",
    ...(isAssembly ? ["What components are in this assembly?", "Highlight all bearings"] : []),
    ...(selectedOccurrenceId ? ["What is this selected component?", "Where else is this selected component used?"] : []),
  ];
  const send = async (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || inFlightRef.current || historical) return;
    inFlightRef.current = true;
    setBusy(true); setError("");
    try {
      const response = await api<AssistantMessageResponse>("/ai/assistant/messages", "POST", {
        conversation_id: conversationId ?? null,
        message: trimmed,
        context: {
          workspace: { page: "item-revision" },
          item: { item_id: itemId, revision_id: revisionId },
          ...(isAssembly ? { assembly: {
            item_id: itemId, revision_id: revisionId,
            ...(selectedOccurrenceId ? { selected_occurrence_id: selectedOccurrenceId } : {}),
          } } : {}),
          ...(selectedOccurrenceId ? { selection: { object_ids: [selectedOccurrenceId] } } : {}),
        },
      });
      if (!activeRef.current) return;
      setConversationId(response.conversation_id);
      setExchanges(current => [...current, { id: response.message_id, question: trimmed, response }]);
      setQuestion("");
      const viewerAction = response.actions.find((action): action is AssistantViewerAction =>
        action.type === "HIGHLIGHT_OCCURRENCES" || action.type === "ISOLATE_OCCURRENCES");
      if (viewerAction) { onViewerAction({ ...viewerAction, id: response.message_id }); close(); }
    } catch (reason) {
      if (activeRef.current) setError(reason instanceof Error ? reason.message : "Unable to ask Modelryx");
    } finally { inFlightRef.current = false; if (activeRef.current) setBusy(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void send(question); };
  return <>
    <button ref={triggerRef} className="revision-assistant-trigger" type="button"
      aria-expanded={open} aria-controls="revision-assistant-panel" onClick={() => setOpen(value => !value)}>
      <span aria-hidden="true">✦</span> AI Assistant
    </button>
    {open && <aside id="revision-assistant-panel" className="revision-assistant-panel"
      aria-label={`AI Assistant for ${itemNumber}`} onKeyDown={event => { if (event.key === "Escape") close(); }}>
      <header className="revision-assistant-header">
        <div><strong>AI Assistant</strong><span>{itemNumber} · Current revision</span></div>
        <button type="button" aria-label="Close AI Assistant" onClick={close}>×</button>
      </header>
      <div className="revision-assistant-body" aria-live="polite">
        {historical && <p className="revision-assistant-notice">This assistant reads the current revision. <Link to={`/items/${encodeURIComponent(itemId)}/revisions/${encodeURIComponent(revisionId)}`}>Open current version</Link> to ask about it.</p>}
        {!historical && exchanges.length === 0 && <p className="revision-assistant-empty">Ask about this item, its revisions, or the selected assembly component. Answers use authorized Modelryx data.</p>}
        {exchanges.map(exchange => <div className="revision-assistant-exchange" key={exchange.id}>
          <div className="revision-assistant-question"><span>You</span><p>{exchange.question}</p></div>
          <article className="revision-assistant-answer"><span>Modelryx</span><p>{exchange.response.answer}</p>
            {exchange.response.references.map((reference, index) => {
              const path = referencePath(exchange.response, reference);
              return path ? <Link key={`${exchange.id}-${index}`} className="revision-assistant-reference" to={path}>{reference.label}<span>Open →</span></Link> :
                <div key={`${exchange.id}-${index}`} className="revision-assistant-reference is-static">{reference.label}</div>;
            })}
            {exchange.response.actions.filter((action): action is Extract<AssistantAction, { type: "OPEN_COMPARISON" }> => action.type === "OPEN_COMPARISON")
              .map(action => <Link key={action.comparison_id} className="revision-assistant-reference"
                to={`/items/${encodeURIComponent(action.item_id)}/compare?${new URLSearchParams({ from: action.from_revision_id, to: action.to_revision_id })}`}>
                Open revision comparison<span>Open →</span></Link>)}
            {exchange.response.warnings.map((warning, index) => <p className="revision-assistant-warning" key={`${exchange.id}-warning-${index}`}>{warning}</p>)}
          </article>
        </div>)}
        {busy && <p role="status" className="revision-assistant-progress">Checking Modelryx data…</p>}
        {error && <p role="alert" className="revision-assistant-error">{error}</p>}
      </div>
      {!historical && exchanges.length === 0 && !question.trim() && <div className="revision-assistant-suggestions" aria-label="Suggested questions">
        {suggestions.map(suggestion => <button type="button" key={suggestion} disabled={busy} onClick={() => void send(suggestion)}>{suggestion}</button>)}
      </div>}
      <form className="revision-assistant-form" onSubmit={submit}>
        <label htmlFor="revision-assistant-question">Ask about this revision</label>
        <textarea id="revision-assistant-question" ref={inputRef} value={question} rows={3} maxLength={4000}
          disabled={historical || busy} onChange={event => setQuestion(event.target.value)}
          placeholder={historical ? "Open the current version to ask" : "What changed from the previous revision?"} />
        <div><small>Read-only answers · Check linked evidence before engineering decisions.</small>
          <Button type="submit" disabled={historical || busy || !question.trim()}>{busy ? "Working…" : "Ask"}</Button></div>
      </form>
    </aside>}
  </>;
}
