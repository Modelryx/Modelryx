# AI provider adapters

Modelryx supports OpenAI models (the provider behind ChatGPT), Anthropic Claude,
Google Gemini, and an existing OpenAI-compatible gateway. Configure one chat
provider per server deployment. Existing callers keep using `AiGateway.answer()`
and `AiGateway.structured_chat()`; no frontend API change is required.

## Configuration

Set these values in the server environment or the deployment `.env`, then restart
the API and any backend workers that use AI:

```dotenv
MODELRYX_AI_CHAT_PROVIDER=openai
MODELRYX_AI_CHAT_API_KEY=replace-with-your-provider-api-key
MODELRYX_AI_CHAT_MODEL=replace-with-an-enabled-model-id
MODELRYX_AI_CHAT_MAX_OUTPUT_TOKENS=4096
MODELRYX_AI_REQUEST_TIMEOUT_SECONDS=60
```

| Provider setting | Service | Default base URL | Required model capability |
| --- | --- | --- | --- |
| `openai` | OpenAI | `https://api.openai.com/v1` | Chat Completions with JSON-schema output |
| `anthropic` | Claude | `https://api.anthropic.com/v1` | Messages with forced tool use |
| `gemini` | Gemini Developer API | `https://generativelanguage.googleapis.com/v1beta` | generateContent with JSON-schema output |
| `openai_compatible` | Existing/local gateway | No default; configure explicitly | Compatible chat/completions and structured output |

Use the model identifier enabled for your provider account. Switching providers
also requires changing the model and API key; a model name from another provider
is not portable. Keys are server-side API credentials. This integration does not
log in to the ChatGPT, Claude, or Gemini consumer applications.

For Claude, change `MODELRYX_AI_CHAT_PROVIDER` to `anthropic` and supply its model
and key. For Gemini, use `gemini` with its model and key. Native providers require
an explicit `MODELRYX_AI_CHAT_API_KEY` and model before chat is enabled.

`MODELRYX_AI_CHAT_BASE_URL` optionally overrides the selected provider's base URL,
including the API version prefix shown above. It must speak that provider's
protocol; this setting is for trusted deployment configuration. Native adapters
never inherit the legacy gateway URL or key.

## Existing gateway and embeddings

The default provider remains `openai_compatible`. Existing settings continue to work:

```dotenv
MODELRYX_AI_GATEWAY_BASE_URL=https://your-gateway.example/v1
MODELRYX_AI_GATEWAY_API_KEY=replace-me
MODELRYX_AI_CHAT_MODEL=your-chat-model
MODELRYX_AI_EMBEDDING_MODEL=your-embedding-model
MODELRYX_AI_EMBEDDING_DIMENSIONS=1536
```

In compatible mode, chat-specific URL/key settings override their legacy gateway
equivalents. A custom compatible endpoint can omit a key for a local deployment.
Without an explicit compatible URL, no chat request is sent.

Embeddings remain independently configured through `MODELRYX_AI_GATEWAY_*` and
`MODELRYX_AI_EMBEDDING_*`. This change adds native **chat** adapters; it does not
add native Claude or Gemini embedding adapters or alter stored vector spaces.
For example, Claude chat can coexist with an OpenAI-compatible embedding service.
Leave the embedding model empty for structured/lexical operation without vectors.
The database still requires 1536-dimensional embeddings; changing chat providers
does not require reindexing or changing embedding configuration.

## Behavior and limits

- OpenAI uses Chat Completions. Native requests use `max_completion_tokens` and
  omit temperature for reasoning-model compatibility. Compatible requests retain
  temperature zero and use `max_tokens`.
- Native OpenAI requests use non-strict JSON-schema guidance because domain
  schemas include optional fields and open metadata objects. Legacy compatible
  requests retain their strict-schema setting. Both are validated locally.
- Claude structured requests force one synthetic result tool. Its returned input
  is data only; the adapter never executes model-selected operations.
- Gemini uses system instructions and `responseJsonSchema`, authenticates with
  the API-key header, and excludes thought parts from returned answers.
- Every structured result must be an object matching the supplied JSON Schema.
  Domain callers retain their additional Pydantic and engineering validation.
- Empty, malformed, refused, safety-blocked, or truncated native responses raise
  normalized errors. Existing feature callers can use their deterministic fallbacks.
  Gateway HTTP/network errors omit raw provider bodies and credentials.
- The timeout applies to each HTTP request. No adapter automatically retries or
  sends data to another provider. Streaming is not supported. Optional bounded
  tool orchestration is described in [tool support](tools.md).

Run `GET /api/v1/ai/status` to inspect chat and embedding availability. Availability
indicates configuration, not a successful provider connectivity check. Validate
your selected model with synthetic data before enabling confidential workloads.
Existing authorization boundaries still apply; source text and questions sent to
the configured provider are engineering-data disclosures.

## Protocol references

- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
