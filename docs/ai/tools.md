# Read-only AI tools

OpenAI, Claude, Gemini, and compatible chat gateways can propose calls to a shared
server-owned engineering tool catalog. Modelryx validates and authorizes each call;
models never receive database or worker access.

## Enable and use

Configure a [chat provider](providers.md), then enable tools and restart the backend:

```dotenv
MODELRYX_AI_TOOL_CALLING_ENABLED=true
MODELRYX_AI_TOOL_MAX_CALLS=6
MODELRYX_AI_TOOL_MAX_ROUNDS=3
MODELRYX_AI_TOOL_TIMEOUT_SECONDS=30
```

The feature is disabled by default. The existing workspace assistant uses the same
`POST /api/v1/ai/assistant/messages` endpoint and message/context body. Eligible
requests return the existing response fields with `intent: "TOOL_ASSISTED"`.
References and navigation actions come from server tool results, never model prose.
No database migration is needed; executions use `ai_tool_executions`.

Example body (replace the UUIDs with readable records, or omit item context for search):

```json
{
  "message": "Find aluminum parts heavier than 2 kg and describe the best match",
  "context": {}
}
```

Searches, item summaries, and otherwise unclassified questions can use the tool loop.
Existing deterministic BOM, where-used, viewer, comparison, feature/material, and
validation intents retain their current handlers until equivalent tools are added.
If chat is unconfigured, supported requests continue through the deterministic
assistant. Runtime provider failure returns available verified tool summaries and
a warning; it does not execute another provider or mutate engineering records.

## Initial catalog

| Tool | Arguments | Result |
| --- | --- | --- |
| `search_items` | `request`: existing search contract with limit 1–20, offset 0–1000, no similarity or facets | Readable revisions, recorded material/mass/dimensions, applied request, total, warnings, coverage and truncation |
| `get_item_revision` | Explicit `item_id` and `revision_id` UUIDs | Recorded revision metadata excerpt, content snapshot identity and truncation |

The search request defaults to 20 records. Unknown properties, actor/tenant
arguments, invalid IDs, unsupported options and excessive values are rejected.
Exact filters are passed to the existing engineering-search executor with the
authenticated principal. Revision lookup uses `readable_item_revision`.
Search revision selection retains the existing executor's semantics; the applied
request and returned revision codes identify its scope. No new “latest released”
policy is implied.

An example model call is:

```json
{
  "name": "search_items",
  "arguments": {
    "request": {
      "entity_type": "part",
      "filters": {
        "material": {"family": "aluminum"},
        "mass_kg": {"gt": 2}
      },
      "limit": 20
    }
  }
}
```

## Execution and evidence

1. Authenticate and check conversation ownership through the existing endpoint.
2. Resolve and authorize item/assembly context before sending IDs to the provider.
3. Advertise only registered tools; require a tool on the initial model turn.
4. Validate the call envelope and arguments. Execute allowed handlers serially
   with the server's principal; invalid calls receive a bounded generic error.
5. Issue evidence IDs (`T1`, `T2`, …) for successful results and return native
   provider tool-result messages. Dependent calls can use previous result IDs.
6. Recheck source visibility and snapshot identity before later provider calls and
   before returning an answer. Source changes invalidate the answer and references.
7. Return generated text only when its citation IDs refer to available evidence;
   otherwise use deterministic tool summaries. Citation checks verify membership,
   not whether every generated claim is entailed by its source.
8. Persist validated arguments, status, duration and bounded reference summaries
   for each executed or rejected call, plus the existing assistant audit event.

OpenAI uses function calls and tool messages; Claude uses tool_use/tool_result
blocks; Gemini uses functionCall/functionResponse parts. Gemini continuation
content, including thought signatures, is preserved. The adapter translates
protocols; only the server registry selects executable handlers.

Limits apply per message: at most six calls, three model turns (including a final
answer), 16,000 serialized argument characters per call and 48,000 serialized
result characters. A request with too many calls is stopped before that batch
executes. Duplicate call IDs are rejected. Exhausted limits return summaries with
an explicit incomplete-results warning.

The wall-clock deadline stops new provider/tool work, and provider timeouts are
capped by the remaining budget. Tool SQL statements have a transaction-local
timeout of at most ten seconds and the remaining budget at tool start. These are
cooperative limits: an in-flight synchronous handler or source verification can
finish after the orchestration deadline. No automatic retry occurs.

The current loop uses the new question and fresh workspace context, not stored
conversation text as model input. It supports dependent calls within that request;
cross-message model memory is not added by this change. Results and questions
sent to providers remain subject to the deployment's engineering-data policy.

## Extending the catalog

Add a strict argument model, description, and handler to
`assistant/tools.py`. Handlers must return bounded structured evidence with
server-issued references and snapshot identities, enforce existing visibility,
and avoid commits, mutations, generated SQL, arbitrary URLs and native commands.
Add authorization, error, bounded-result and provider round-trip tests before
advertising the tool. BOM, comparison, document, validation, and confirmed writes
are not registered by this initial increment.
