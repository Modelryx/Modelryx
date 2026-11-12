import { useEffect, useState, type FormEvent } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { Link } from 'react-router-dom';
import { Button } from '../../components/Button';
import { DataTable } from '../../components/DataTable';
import {
  api,
  type BOMAIResponse,
  type BOMDuplicateResponse,
  type BOMReadinessResponse,
  type BOMSummary,
  type StandardizationCandidate,
  type StandardizationResponse,
} from '../../workflow-api';
import './bomIntelligence.css';

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bom-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Coverage({ label, percent, detail }: { label: string; percent: number; detail: string }) {
  return (
    <div className="bom-coverage-row">
      <div>
        <strong>{label}</strong>
        <span>{Math.round(percent)}%</span>
      </div>
      <progress
        max={100}
        value={Math.max(0, Math.min(100, percent))}
        aria-label={`${label} coverage`}
      />
      <small>{detail}</small>
    </div>
  );
}

function FactList({ rows }: { rows: Array<[string, number | string]> }) {
  return (
    <dl className="bom-fact-list">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const candidateColumns: ColumnDef<StandardizationCandidate>[] = [
  {
    id: 'pair',
    header: 'Candidate pair',
    size: 210,
    accessorFn: (row) => `${row.item_a} ${row.item_b}`,
    cell: ({ row }) => (
      <div className="bom-candidate-pair">
        <strong>
          {row.original.item_a} ↔ {row.original.item_b}
        </strong>
        <span>{row.original.classification.replaceAll('_', ' ')}</span>
      </div>
    ),
  },
  {
    id: 'score',
    header: 'Score',
    size: 90,
    accessorFn: (row) => row.score,
    cell: ({ row }) => `${Math.round(row.original.score * 100)}%`,
  },
  {
    id: 'coverage',
    header: 'Evidence coverage',
    size: 130,
    accessorFn: (row) => row.score_coverage,
    cell: ({ row }) => `${Math.round(row.original.score_coverage * 100)}%`,
  },
  {
    id: 'evidence',
    header: 'Evidence and limitations',
    size: 300,
    enableSorting: false,
    cell: ({ row }) => (
      <div className="bom-candidate-evidence">
        {row.original.reasons.map((reason) => (
          <p key={reason}>{reason}</p>
        ))}
        {row.original.limitations.map((limitation) => (
          <p className="bom-limitation" key={limitation}>
            {limitation}
          </p>
        ))}
      </div>
    ),
  },
  { id: 'advisory', header: 'Advisory', size: 240, accessorFn: (row) => row.advisory },
];

export function BOMIntelligenceSection({ revisionId }: { revisionId: string }) {
  const [summary, setSummary] = useState<BOMSummary | null>(null);
  const [duplicates, setDuplicates] = useState<BOMDuplicateResponse | null>(null);
  const [readiness, setReadiness] = useState<BOMReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<BOMAIResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [questionError, setQuestionError] = useState('');
  const [candidates, setCandidates] = useState<StandardizationResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError('');
    setSummary(null);
    setDuplicates(null);
    setReadiness(null);
    setAnswer(null);
    setCandidates(null);
    setQuestionError('');
    setAnalysisError('');
    void Promise.all([
      api<BOMSummary>(`/item-revisions/${encodeURIComponent(revisionId)}/bom/summary`),
      api<BOMDuplicateResponse>(
        `/item-revisions/${encodeURIComponent(revisionId)}/bom/duplicate-pairs`
      ),
      api<BOMReadinessResponse>(
        `/item-revisions/${encodeURIComponent(revisionId)}/bom/readiness`,
        'POST',
        {
          allowed_lifecycle_states: ['READY', 'READY_WITH_WARNINGS'],
          require_drawings: false,
          require_material: false,
          require_mass: false,
        }
      ),
    ])
      .then(([loadedSummary, loadedDuplicates, loadedReadiness]) => {
        if (!active) return;
        setSummary(loadedSummary);
        setDuplicates(loadedDuplicates);
        setReadiness(loadedReadiness);
      })
      .catch((reason) => {
        if (active)
          setLoadError(
            reason instanceof Error ? reason.message : 'Unable to load BOM intelligence'
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revisionId, loadAttempt]);

  const ask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!question.trim() || asking) return;
    setAsking(true);
    setQuestionError('');
    setAnswer(null);
    try {
      setAnswer(
        await api<BOMAIResponse>(
          `/item-revisions/${encodeURIComponent(revisionId)}/bom/ai-query`,
          'POST',
          { message: question.trim() }
        )
      );
    } catch (reason) {
      setQuestionError(reason instanceof Error ? reason.message : 'BOM question failed');
    } finally {
      setAsking(false);
    }
  };

  const analyzeStandardization = async () => {
    if (analyzing) return;
    setAnalyzing(true);
    setAnalysisError('');
    try {
      setCandidates(
        await api<StandardizationResponse>(
          `/item-revisions/${encodeURIComponent(revisionId)}/bom/standardization`,
          'POST',
          { minimum_score: 0.7, limit: 50 }
        )
      );
    } catch (reason) {
      setAnalysisError(
        reason instanceof Error ? reason.message : 'Standardization analysis failed'
      );
    } finally {
      setAnalyzing(false);
    }
  };

  const snapshotMismatch =
    !!summary &&
    ((!!duplicates && duplicates.snapshot_id !== summary.snapshot_id) ||
      (!!readiness && readiness.snapshot_id !== summary.snapshot_id));
  return (
    <section className="bom-intelligence" aria-labelledby="bom-intelligence-title">
      <header className="bom-page-heading">
        <div>
          <h2 id="bom-intelligence-title">BOM Intelligence</h2>
          <p>Assembly facts, coverage, and advisory analysis for this revision.</p>
        </div>
      </header>
      {loading ? (
        <div className="bom-state" role="status">
          Loading BOM analysis…
        </div>
      ) : loadError ? (
        <div className="bom-state bom-state-error" role="alert">
          <span>{loadError}</span>
          <Button variant="secondary" onClick={() => setLoadAttempt((value) => value + 1)}>
            Retry
          </Button>
        </div>
      ) : (
        summary && (
          <>
            {snapshotMismatch && (
              <p className="bom-notice" role="status">
                The assembly changed while analysis loaded. Refresh to see one consistent version.{' '}
                <Button variant="secondary" onClick={() => setLoadAttempt((value) => value + 1)}>
                  Refresh
                </Button>
              </p>
            )}
            <div className="bom-metrics" aria-label="Assembly totals">
              <Metric
                label="Unique component revisions"
                value={summary.unique_component_revisions}
              />
              <Metric label="Occurrences" value={summary.total_occurrences} />
              <Metric label="Effective quantity" value={summary.total_quantity} />
              <Metric label="Subassemblies" value={summary.subassemblies} />
            </div>

            <div className="bom-primary-grid">
              <section className="bom-panel bom-readiness" aria-labelledby="bom-readiness-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Current checks</span>
                    <h3 id="bom-readiness-title">Readiness</h3>
                  </div>
                  <span
                    className={`bom-status bom-status-${readiness?.status.toLowerCase().replaceAll('_', '-') ?? 'unknown'}`}
                  >
                    {readiness?.status.replaceAll('_', ' ') ?? 'Unavailable'}
                  </span>
                </div>
                <p className="bom-panel-description">
                  The check uses processing state; drawing, material, and mass gaps are shown as
                  coverage information below.
                </p>
                {readiness?.findings.length ? (
                  <ul className="bom-findings">
                    {readiness.findings.map((finding) => (
                      <li key={finding.code}>
                        <span
                          className={`bom-severity bom-severity-${finding.severity.toLowerCase()}`}
                        >
                          {finding.severity}
                        </span>
                        <span>{finding.message}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="bom-empty-inline">No readiness findings were reported.</p>
                )}
                {!!readiness?.limitations.length && (
                  <details className="bom-limitations">
                    <summary>Check limitations ({readiness.limitations.length})</summary>
                    <ul>
                      {readiness.limitations.map((limitation) => (
                        <li key={limitation}>{limitation}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </section>
              <section className="bom-panel" aria-labelledby="bom-coverage-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Completeness</span>
                    <h3 id="bom-coverage-title">Data coverage</h3>
                  </div>
                </div>
                <Coverage
                  label="Drawings"
                  percent={summary.documentation.coverage_percent}
                  detail={`${summary.documentation.with_drawing} with drawing · ${summary.documentation.without_drawing} without`}
                />
                <Coverage
                  label="Material"
                  percent={summary.material_coverage.coverage_percent}
                  detail={`${summary.material_coverage.missing_quantity} effective quantity missing material`}
                />
                <Coverage
                  label="Mass"
                  percent={summary.mass.coverage_percent}
                  detail={`${summary.mass.missing_quantity} effective quantity missing mass`}
                />
                <div className="bom-total-mass">
                  <span>Calculated part mass</span>
                  <strong>{summary.mass.total_mass_kg.toFixed(3)} kg</strong>
                </div>
              </section>
            </div>

            <div className="bom-secondary-grid">
              <section className="bom-panel" aria-labelledby="bom-lifecycle-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Composition</span>
                    <h3 id="bom-lifecycle-title">Lifecycle and reuse</h3>
                  </div>
                </div>
                <FactList
                  rows={[
                    ...Object.entries(summary.lifecycle).map(([state, count]): [string, number] => [
                      state.replaceAll('_', ' '),
                      count,
                    ]),
                    ['Reused components', summary.reuse.reused_components],
                    ['Single-use components', summary.reuse.single_use_components],
                  ]}
                />
              </section>
              <section className="bom-panel" aria-labelledby="bom-materials-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Composition</span>
                    <h3 id="bom-materials-title">Materials</h3>
                  </div>
                </div>
                {Object.keys(summary.materials).length ? (
                  <FactList rows={Object.entries(summary.materials)} />
                ) : (
                  <p className="bom-empty-inline">No material values are recorded.</p>
                )}
              </section>
            </div>

            <div className="bom-secondary-grid">
              <section className="bom-panel" aria-labelledby="bom-duplicates-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Advisory</span>
                    <h3 id="bom-duplicates-title">Potential duplicates</h3>
                  </div>
                  <span className="bom-count">{duplicates?.total ?? 0} pairs</span>
                </div>
                {duplicates?.pairs.length ? (
                  <>
                    <ul className="bom-pair-list">
                      {duplicates.pairs.slice(0, 5).map((pair) => (
                        <li key={`${pair.revision_a_id}-${pair.revision_b_id}`}>
                          <strong>
                            {pair.item_a} ↔ {pair.item_b}
                          </strong>
                          <span>
                            {pair.classification.replaceAll('_', ' ')}
                            {pair.score === null ? '' : ` · ${Math.round(pair.score * 100)}% score`}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {duplicates.total > 5 && (
                      <p className="bom-panel-description">
                        Showing 5 of {duplicates.total} pairs.
                      </p>
                    )}
                  </>
                ) : (
                  <p className="bom-empty-inline">No BOM-context candidate pairs were found.</p>
                )}
                <p className="bom-advisory">
                  Candidate pairs require engineering review before any substitution.
                </p>
              </section>
              <section className="bom-panel" aria-labelledby="bom-question-title">
                <div className="bom-panel-heading">
                  <div>
                    <span className="bom-kicker">Explore</span>
                    <h3 id="bom-question-title">Ask about this BOM</h3>
                  </div>
                </div>
                <form className="bom-question-form" onSubmit={(event) => void ask(event)}>
                  <label htmlFor="bom-question">Question</label>
                  <div>
                    <input
                      id="bom-question"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      placeholder="Which components have no drawing?"
                    />
                    <Button type="submit" disabled={asking || !question.trim()}>
                      {asking ? 'Asking…' : 'Ask'}
                    </Button>
                  </div>
                </form>
                {questionError && (
                  <p className="bom-inline-error" role="alert">
                    {questionError}
                  </p>
                )}
                {answer && (
                  <div className="bom-answer">
                    <p className="bom-answer-text">{answer.answer}</p>
                    <p className="bom-panel-description">
                      {answer.mode === 'AI' ? 'AI-assisted' : 'Deterministic'} interpretation ·{' '}
                      {answer.result.returned} shown
                      {answer.result.truncated ? ' · Results truncated' : ''}
                    </p>
                    {answer.result.nodes.length > 0 && (
                      <ul>
                        {answer.result.nodes.map((node) => (
                          <li key={node.occurrence_id}>
                            <Link
                              to={`/items/${encodeURIComponent(node.item_id)}/revisions/${encodeURIComponent(node.revision_id)}`}
                            >
                              {node.item_number} · Rev {node.revision} · {node.item_name}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                    {answer.warnings.map((warning) => (
                      <p className="bom-limitation" key={warning}>
                        {warning}
                      </p>
                    ))}
                  </div>
                )}
              </section>
            </div>

            <section
              className="bom-panel bom-standardization"
              aria-labelledby="bom-standardization-title"
            >
              <div className="bom-panel-heading">
                <div>
                  <span className="bom-kicker">Advisory</span>
                  <h3 id="bom-standardization-title">Standardization candidates</h3>
                  <p className="bom-panel-description">
                    Similarity scores and evidence help prioritize review; candidates are not proven
                    interchangeable.
                  </p>
                </div>
                <Button
                  variant="secondary"
                  disabled={analyzing}
                  onClick={() => void analyzeStandardization()}
                >
                  {analyzing ? 'Analyzing…' : candidates ? 'Run again' : 'Analyze candidates'}
                </Button>
              </div>
              {analysisError && (
                <p className="bom-inline-error" role="alert">
                  {analysisError}
                </p>
              )}
              {candidates &&
                (candidates.candidates.length ? (
                  <>
                    <p className="bom-result-count">
                      {candidates.returned} of {candidates.total} candidates returned
                      {candidates.truncated ? ' · Results truncated' : ''}
                    </p>
                    <DataTable
                      data={candidates.candidates}
                      columns={candidateColumns}
                      getRowId={(row) => `${row.revision_a_id}:${row.revision_b_id}`}
                      density="compact"
                      initialPageSize={10}
                    />
                  </>
                ) : (
                  <p className="bom-empty-inline">No candidates met the evidence threshold.</p>
                ))}
            </section>
          </>
        )
      )}
    </section>
  );
}
