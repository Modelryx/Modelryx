import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/Button';
import { useResource } from '../components/ResourceState';
import './AiAssistant.css';

type Source = {
  id: string;
  item_id: string;
  revision_id: string;
  label: string;
  item_number: string;
  revision_code: string;
  title: string;
  excerpt: string;
};

type Answer = { answer: string; sources: Source[] };
type AiStatus = {
  gateway_configured: boolean;
  embeddings_configured: boolean;
  indexed_revisions: number;
};

export function AiAssistant() {
  const status = useResource<AiStatus>('/ai/status');
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<Answer | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setError('');
    try {
      setResult(await api<Answer>('/ai/query', 'POST', { question: question.trim() }));
    } catch (reason) {
      setResult(null);
      setError(reason instanceof Error ? reason.message : 'Unable to answer the question');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ai-assistant">
      <h1>AI Assistant</h1>
      <p className="ai-intro">
        Search engineering data across revisions. For questions about one open revision, use the AI
        Assistant button in its workspace. Feature searches include parent assemblies.
      </p>
      {status.data && (
        <p className="ai-index-status" role="status">
          {status.data.indexed_revisions} indexed revisions ·{' '}
          {status.data.gateway_configured
            ? 'AI explanation available'
            : 'Deterministic results available'}
        </p>
      )}
      {status.error && (
        <p className="ai-index-status" role="status">
          Index status unavailable. You can still try a question.
        </p>
      )}
      <form onSubmit={ask}>
        <label htmlFor="ai-question">Question</label>
        <textarea
          id="ai-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Find parts with holes"
          rows={4}
        />
        <Button disabled={busy || !question.trim()} type="submit">
          {busy ? 'Searching…' : 'Ask Modelryx'}
        </Button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="ai-result">
          <h2>Answer</h2>
          <p className="ai-answer">{result.answer}</p>
          <h2>Evidence</h2>
          <ol className="ai-sources">
            {result.sources.map((source) => (
              <li key={source.id}>
                <div>
                  <strong>[{source.label}]</strong>{' '}
                  <Link
                    to={`/items/${encodeURIComponent(source.item_id)}/revisions/${encodeURIComponent(source.revision_id)}`}
                  >
                    {source.item_number}, revision {source.revision_code}
                  </Link>{' '}
                  — {source.title}
                </div>
                <p>{source.excerpt}</p>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
