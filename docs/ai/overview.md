# AI subsystem overview

Modelryx's AI subsystem is a set of bounded consumers of authoritative engineering data. Most
capabilities work deterministically without a model provider. An optional OpenAI-compatible gateway
adds embeddings, natural-language interpretation, explanations, and answers where configured.

## Architecture

```text
Authoritative Modelryx records
Items · Revisions · Artifacts · metadata · BOM · validation · approvals
                              |
              +---------------+----------------+
              |                                |
              v                                v
   deterministic domain services      disposable search projections
   comparison · validation · BOM       lexical documents · embeddings
   action validation · CAD plans       knowledge chunks · scenario catalog
              |                                |
              +---------------+----------------+
                              v
                 optional model-assisted layer
                 interpretation · explanation
                    retrieval-grounded answers
                              |
                              v
                  typed response or proposal
                  never direct product mutation
```

## Capability map

| Capability | Deterministic foundation | Optional model assistance |
| --- | --- | --- |
| Engineering search | Validated structured request, SQLAlchemy query construction, stable ranking and pagination | Natural-language interpretation and embeddings |
| Assistant | Allowlisted intents and tools, authorized server context | Intent interpretation and bounded response generation |
| Engineering knowledge | Authorized source graph, lexical retrieval, evidence bundles | Embeddings and grounded synthesis |
| Revision comparison | Versioned deterministic diff engine | Evidence-cited explanation |
| Validation | Versioned rules and immutable findings | Finding explanation only |
| Assembly intelligence | Authoritative BOM traversal and deterministic analysis | Bounded BOM question answering |
| Controlled actions | Typed proposal, confirmation, state token, authorization, idempotency | Proposal assistance; never direct execution |
| Controlled CAD operations | Inspected native IDs, immutable plans, isolated worker execution, review and commit | Explanation or target ranking within inspected choices |

## Provider configuration

Configure the optional gateway through server environment settings:

```dotenv
MODELRYX_AI_GATEWAY_BASE_URL=https://your-gateway.example/v1
MODELRYX_AI_GATEWAY_API_KEY=replace-me
MODELRYX_AI_CHAT_MODEL=your-chat-model
MODELRYX_AI_EMBEDDING_MODEL=your-embedding-model
MODELRYX_AI_EMBEDDING_DIMENSIONS=1536
```

The gateway must provide compatible `/chat/completions` and `/embeddings` operations. The current
database vector schema requires 1536 dimensions. Without a gateway, indexing, lexical retrieval,
structured search, comparison, validation, BOM analysis, action controls, and CAD plans continue to
work; vector retrieval and generated explanations are unavailable or use deterministic fallbacks.

`GET /api/v1/ai/status` reports whether chat and embedding configuration is available and how many
revision documents are indexed.

## Retrieval projections

Modelryx derives searchable documents from readable Revision metadata, Artifacts, BOM structure,
and other authorized sources. PostgreSQL full-text search remains available without embeddings;
pgvector similarity is added when embeddings are configured. Retrieval projections are disposable
and rebuildable. They are not authoritative engineering records.

The legacy `/api/v1/ai` index, search, and query endpoints provide revision-oriented RAG. The
`/api/v1/knowledge` APIs provide richer typed evidence sources and relations. Engineering search has
its own validated request model and must not be confused with unconstrained RAG.

## Trust and safety boundaries

- Authorization and tenant visibility are enforced before retrieval and again before returning
  source records.
- Models never emit SQL, storage paths, arbitrary URLs, native CAD commands, or executable code.
- Retrieved documents, CAD metadata, attachment text, and model output are untrusted data.
- Every mutation uses a typed server-owned tool, fresh authoritative state, explicit confirmation,
  idempotency, and audit.
- AI explanations cannot change deterministic findings, comparison records, BOM data, lifecycle, or
  approval state.
- Provider credentials remain server-side and are never returned to the browser.
- An unavailable gateway must not invalidate verified CAD processing or committed engineering data.

## Data lifecycle

Successful CAD publication, including metadata and preview regeneration, attempts to refresh both
revision AI documents and the engineering knowledge index. Each index runs independently after the
CAD result is committed; an indexing failure is logged and does not fail the CAD operation. Missing
embeddings can be retried by reindexing the revision once the provider is available.

Existing projections can also be refreshed explicitly through both endpoints:

- `POST /api/v1/ai/items/{item_id}/revisions/{revision_id}/index`
- `POST /api/v1/knowledge/revisions/{revision_id}/index`

Administrator backfill through `POST /api/v1/ai/index-missing` creates only missing AI documents;
it does not refresh existing AI projections or populate the knowledge index. Changes to a
Revision generate new projections rather than mutating historical engineering evidence. Search
session and feedback retention is controlled by server settings; operational metrics exclude raw
query text, credentials, and user-identifying labels.

## Documentation map

See the [AI documentation index](README.md) for capability contracts and the
For the engineering agent architecture, see [AI subsystem overview](../ai/overview.md).
