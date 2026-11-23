# AI Documentation

Modelryx's AI subsystem is a set of bounded consumers of authoritative engineering data. Structured search, comparisons, validation, and BOM analysis work without a model provider. Optional OpenAI, Anthropic Claude, Gemini, or compatible chat providers add interpretation and generated answers; an independently configured OpenAI-compatible embedding gateway enables vector retrieval.

## Core Documentation

- **[tools.md](./tools.md)** — Read-only tool catalog, provider execution loop, limits, and enablement
- **[providers.md](./providers.md)** — Configure OpenAI, Claude, Gemini, or a compatible chat gateway
- **[design-spec.md](./design-spec.md)** — Proposed AI design specification, architecture review, contracts, and delivery gates
- **[overview.md](./overview.md)** — Architecture, capability map, trust boundaries, and configuration
- **[assistant.md](./assistant.md)** — Context-aware assistant implementation, intents, and tool lifecycle
- **[engineering-search.md](./engineering-search.md)** — Deterministic search behavior, validation gates, and evaluation framework

## Quick Reference

### Key Capabilities

| Capability | Deterministic Foundation | Optional Model Assistance |
| --- | --- | --- |
| Engineering search | Validated structured request, SQLAlchemy query construction, stable ranking | Natural-language interpretation and embeddings |
| Assistant | Allowlisted intents and tools, authorized server context | Intent interpretation and bounded response generation |
| Revision comparison | Versioned deterministic diff engine | Evidence-cited explanation |

### Configuration

For a compatible chat and embedding gateway, set the following server environment values. See [provider configuration](providers.md) for native chat adapters and separate chat credentials:

```dotenv
MODELRYX_AI_GATEWAY_BASE_URL=https://your-gateway.example/v1
MODELRYX_AI_GATEWAY_API_KEY=replace-me
MODELRYX_AI_CHAT_MODEL=your-chat-model
MODELRYX_AI_EMBEDDING_MODEL=your-embedding-model
MODELRYX_AI_EMBEDDING_DIMENSIONS=1536
```

### API Endpoints

- `POST /api/v1/ai/assistant/messages` — Assistant message handler
- `GET /api/v1/ai/assistant/conversations/{conversation_id}` — Conversation history
- `GET /api/v1/ai/status` — Gateway and indexing status
- `POST /api/v1/ai/items/{item_id}/revisions/{revision_id}/index` — Refresh revision indexing

## Design Principles

- **Deterministic first**: Structured search and engineering analysis work without a provider; generated answers and vector retrieval require their corresponding configuration.
- **Authorization enforced**: Access control is applied before retrieval and at response time.
- **Never mutative**: AI explanations cannot change engineering findings, lifecycle, or approval state.
- **Audited and bounded**: Optional tool calling uses an allowlisted read-only catalog, validated arguments, execution limits, and audit records. It is disabled by default; see [tool configuration](tools.md).

## See Also

- [Platform Architecture](../architecture/platform-architecture.md) — System overview
- [PartMetadata v1](../architecture/partmetadata-v1.md) — Metadata schema
