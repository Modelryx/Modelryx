# Assistant

This document describes the implemented, context-aware assistant and its read-only command boundary.
The phase labels below identify delivery increments; they are not separate runtime architectures.

## Status

| Sub-phase | Status |
| --- | --- |
| 3.1A Assistant foundation | Implemented |
| 3.1B Embedded assistant UI | Implemented |
| 3.2 Revision intelligence | Implemented |
| 3.3 Relationship intelligence | Implemented |
| 3.4 Search integration | Implemented |
| 3.5 Validation assistant | Implemented |
| 3.6 Viewer-aware assistant | Implemented |
| 3.7 Controlled actions | Deferred pending read-only maturity |

## Phase 3.1A implementation

Phase 3.1A establishes a context-aware, read-only assistant boundary without making an AI provider
part of Modelryx domain logic. The API is available at `POST /api/v1/ai/assistant/messages` and
accepts an optional conversation UUID, a user message, and a strict `AssistantContext` containing
only validated identifiers. Unknown context properties are rejected.

The initial deterministic intent allowlist supports:

- `ITEM_SUMMARY`, executed by the read-only `get_item` tool;
- `ITEM_METADATA`, executed by the read-only `get_item_metadata` tool.

Both tools resolve the current item and revision on the server and apply the existing revision
visibility policy. Context identifiers do not grant access. The tools return structured references
and navigation actions in addition to a concise answer. Missing material is reported as missing and
is never inferred.

Migration `0032_phase31a_assistant` creates separate assistant persistence:

- `ai_conversations` stores user-owned workspace conversations;
- `ai_messages` stores user and assistant messages with a context snapshot per message;
- `ai_tool_executions` stores the allowlisted tool, validated arguments, status, latency, and a
  bounded structured result summary.

Assistant message handling also records a normal Modelryx audit event and returns the request trace
identifier. `GET /api/v1/ai/assistant/conversations/{conversation_id}` returns conversation history
only to its owner.

The foundation does not depend on a configured LLM. Authoritative Modelryx data produces useful
answers deterministically. A provider can later improve phrasing and intent interpretation without
becoming the source of engineering truth.

## Current limitation

The assistant does not directly execute controlled actions. Action proposals and confirmation are
and is reserved for future phases.

## Phase 3.1B implementation

Phase 3.1B embeds a reusable, collapsible assistant panel directly in the item-revision workspace.
It replaces the need to restate the current item and revision while retaining the existing standalone
AI retrieval page for broad indexed-data questions.

The React `WorkspaceContextProvider` owns a small identifier-only context. The item-revision page
publishes its page, item UUID, and revision UUID and clears them when the workspace unmounts. The 3D
viewer also publishes selected occurrence identifiers. The assistant reads a fresh snapshot for every
message, so navigation and selection changes take precedence over earlier conversation context.

The panel provides:

- context-sensitive starter questions for the Phase 3.1A intent allowlist;
- basic in-workspace conversation history and busy/error states;
- structured Modelryx reference cards;
- navigation using allowlisted `OPEN_ITEM` and `OPEN_REVISION` actions;
- a collapsed state that returns workspace width to the engineering detail view;
- responsive stacking on narrower screens.

The browser never constructs arbitrary navigation from assistant text. It interprets only the action
types defined by the shared frontend response contract. The frontend production build validates the
new context and assistant component integration.

The new `front-end` revision workspace also exposes this assistant in a collapsible drawer. Its 3D
preview translates selected render occurrences to managed BOM occurrence IDs only when the server's
occurrence map has a unique match; unmapped selections do not become assistant context. Typed viewer
actions are translated back through the same map. The assistant is disabled while a historical
content version is selected because the current assistant API resolves the live Revision and does not
accept a content-snapshot context. Conversation history currently remains in the open workspace panel;
the backend can read a known conversation by ID but has no conversation-list endpoint yet.

## Phase 3.2 implementation

Phase 3.2 adds two deterministic, read-only assistant intents:

- `REVISION_HISTORY` invokes `get_revision_history` and lists every revision visible to the current
  user in a stable timeline;
- `COMPARE_REVISIONS` invokes `compare_revisions`, using explicit revision codes from questions such
  as “What changed from B to C?” or the previous readable revision when the user asks what changed
  from the current revision.

Revision comparison operates on authoritative Modelryx records. It compares lifecycle state,
flattened revision or derived metadata, and verified artifact identities and SHA-256 hashes. Results
include actual old and new metadata values plus added, removed, or modified filenames. The bounded
tool trace retains up to 25 metadata differences and the structured file changes for audit and future
rendering.

Change reasons are never inferred. The tool checks explicit `change_reason`, `revision_reason`,
`reason`, or `comment` values in revision metadata and revision audit details. If none exists, the
answer states that no reason is recorded.

The embedded assistant exposes revision-history and previous-revision comparison suggestions. The
response contract and UI remain provider-independent and use the same structured revision references
and allowlisted navigation actions introduced in Phase 3.1.

## Phase 3.3 implementation

Phase 3.3 adds three relationship-aware, read-only intents:

- `BOM_QUERY` invokes `get_bom` and returns a bounded recursive assembly summary with quantities,
  component revisions, and navigable component references;
- `WHERE_USED` invokes `get_where_used` and returns every readable direct parent assembly occurrence;
- `SELECTED_COMPONENT` invokes `get_selected_component` and resolves the viewer's selected occurrence
  to its authoritative Modelryx item, revision, quantity, and reference designator.

The tools reuse the existing version-resolved assembly graph and its revision visibility checks.
Selected-occurrence context does not grant access: the assistant rebuilds the current authorized BOM
and rejects an occurrence identifier unless it is present in that graph. This prevents a browser or
model from using an arbitrary occurrence UUID to inspect another assembly.

The workspace context provider now distinguishes assembly context from general item context. When an
assembly revision is open, viewer selection publishes both the bounded object selection and the
explicit selected occurrence. Clearing viewer selection also clears the occurrence pointer. Each
message retains the resulting context snapshot.

The embedded panel adds where-used questions for parts and BOM/selected-component questions for
assemblies. Results use structured `ITEM` or `ASSEMBLY` references and the existing allowlisted open
actions.

## Phase 3.4 implementation

Phase 3.4 connects the embedded assistant to the completed Phase 2 engineering-search service through
two read-only intents:

- `ENGINEERING_SEARCH` invokes `search_parts`, interprets a natural-language request with the Phase 2
  deterministic/provider-assisted interpreter, and executes the resulting strict search model;
- `SIMILAR_PART_SEARCH` invokes `find_similar_parts` using the open revision as the authoritative
  embedding source and excludes that source from its results.

The assistant calls the search service directly inside the authenticated request rather than making
an internal HTTP request. This preserves the current principal, revision visibility policy, scoring,
filter behavior, warnings, and interpretation trace already validated by Phase 2.

Results are returned as typed `SEARCH_RESULT` references with one allowlisted navigation action per
result. The embedded panel matches each clicked reference to its own action, so multi-result searches
open the intended item or revision. Tool traces retain the strict interpretation, bounded result IDs,
scores, match reasons, and warnings without treating generated prose as engineering data.

## Phase 3.5 implementation

Phase 3.5 adds the read-only `VALIDATION_DIAGNOSIS` intent and the allowlisted
`get_validation_result` tool. Questions such as “Why did validation fail?”, “Which file caused the
problem?”, and “What should I fix?” resolve an explicit processing-job UUID from workspace context or
the latest processing job associated with the open readable revision.

The tool reads authoritative `ProcessingJob` state, stage, progress, attempt count, bounded structured
diagnostics, and verified native artifacts from the job's working version. An explicit job must belong
to the current readable revision; a foreign or arbitrary job UUID is rejected. Results use a typed
`VALIDATION` reference and navigate only through the existing allowlisted revision action.

Recommendations are deterministic and stage-specific. They distinguish conversion, validation,
publishing, and worker-availability failures, quote only stored diagnostic evidence, identify linked
source filenames when available, and explicitly report when detailed diagnostics or a verified source
file are missing. Pending and successful jobs are not misrepresented as failures. The audit trace
stores the job identity, lifecycle fields, bounded errors, source filenames, and recommended action.

## Phase 3.6 implementation

Phase 3.6 makes the assistant viewer-aware while retaining application-defined command boundaries.
Selection-sensitive classification resolves “What is this?” and “Where else is this selected
component used?” against the selected occurrence in the current authorized BOM. Selection identifiers
remain references only: an occurrence must be found in the readable assembly graph before its component
or relationships are returned.

Three read-only visualization intents are supported:

- `VIEWER_HIGHLIGHT` matches requested component terms against readable BOM item numbers, names,
  classifications, occurrence keys, source references, and reference designators;
- `VIEWER_ISOLATE` uses the same grounded matching boundary but requests isolation;
- `VIEWER_MODIFIED_COMPONENTS` compares stable occurrence keys in the current and previous readable
  assembly revisions and highlights current occurrences whose component revision or quantity changed,
  plus newly added occurrences.

The server returns only typed `HIGHLIGHT_OCCURRENCES` or `ISOLATE_OCCURRENCES` actions containing at
most 100 authorized occurrence UUIDs. The React workspace command bus recognizes only those action
types. The viewer translates them into its existing selection, isolation, and fit operations; no model
text, selector, or generated JavaScript is executed. Tool traces retain search terms, comparison
revision IDs, matched occurrence IDs, and bounded counts for auditability.


## Hole metadata questions

The `ITEM_METADATA` intent supports current-revision hole questions such as
"Is there a hole in the part?", "Does this part have holes?", and "How many holes
are there?" The authorized tool reads the current extraction directly; no embedding
or chat provider is required. It reports recorded counts and available hole diameters.

Missing, failed, unsupported, or stale extraction data is unknown, never evidence
of absence. A zero count confirms absence only with complete feature coverage;
positive counts from partial coverage are lower bounds. Assembly-level evidence
does not establish the presence or absence of holes in component revisions.


## Controlled CAD feature questions

Current-revision metadata questions also cover fillets, chamfers, pockets, threads,
patterns, extrusions, and revolutions, using the engineering-search feature vocabulary.
For example: "Does this part have chamfers?", "How many pockets are there?", and
"Are there fillets and threads?" Multiple features are answered independently, so an
unknown pocket count cannot become a zero count merely because fillet coverage exists.

The same current-snapshot, extraction-status, and complete/partial coverage rules apply
to every supported feature. Slots, bosses, and ribs produce an explicit unsupported
coverage answer instead of invented counts. Non-feature searches, comparisons, BOM,
and viewer actions retain their existing behavior; assembly feature questions use
recursive counting as described below. Feature answers require no model provider or
reindexing and do not modify engineering data.


## Assembly-aware feature search and counting

The authoritative maintenance rules live beside the implementation in
[`assistant/feature_search.py`](../../server/src/server/modelryx/assistant/feature_search.py).
Both the global `/api/v1/ai/query` page and `/api/v1/ai/assistant/messages` use this service.

- **Global discovery:** start from readable part revisions with positive recorded feature
  evidence and recursively follow their committed parent references. Return matching parts
  and parent assemblies once per revision, retaining distinct occurrence paths as evidence.
  Historical referenced component revisions remain eligible; an Item's latest revision is
  never substituted for a committed BOM reference.
- **Revision scope:** traverse active component occurrences downward through the committed BOM.
  Multiply each part's feature count by every quantity on its path. Cache metadata reads,
  while counting repeated occurrences independently. Suppressed branches are excluded;
  hidden active branches are included.
- **Coverage:** missing, failed, unsupported, stale, and inaccessible evidence prevents a
  complete total. Partial assembly structure also prevents an exact total, even when
  all inspected components have complete counts. Partial positive counts are lower bounds.
  Unknown zero is not absence.
  Assembly-level counts remain separate because they may overlap descendant geometry.
- **Bounds:** cycle detection is path-specific. Traversal is limited to depth 20 and 1,000
  occurrences, and global discovery considers at most 1,000 seed part revisions. Results
  disclose traversal, seed, evidence-display, and result limits.
- **Compatibility:** old worker-created content versions may inherit a BOM only when their
  authoritative source associations are unchanged. A source commit never uses that fallback.

Examples: global "Find parts with holes" discovers parent assemblies too; revision
"How many holes and fillets are in this assembly?" returns independent component totals.
Multiple features use AND by default; an explicit OR requests any matching feature globally.
Global absence searches and additional numeric, material, or text filters are not supported
by this graph path yet and are rejected explicitly instead of silently discarded. The service
works without embeddings or a chat provider and requires no reindexing. Matching components
can be opened through cited revision links; precise feature/face highlighting is not implemented.
