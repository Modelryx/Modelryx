# AI Documentation

Modelryx's AI subsystem is a set of bounded consumers of authoritative engineering data. Most capabilities work deterministically without a model provider. An optional OpenAI-compatible gateway adds embeddings, natural-language interpretation, explanations, and answers where configured.

## Core Documentation

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

Configure an optional AI gateway through server environment settings:

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

- **Deterministic first**: All key features work without an LLM; a provider improves phrasing and interpretation only.
- **Authorization enforced**: Access control is applied before retrieval and at response time.
- **Never mutative**: AI explanations cannot change engineering findings, lifecycle, or approval state.
- **Audited and bounded**: Every mutation uses typed tools, explicit confirmation, idempotency, and audit trails.

## See Also

- [Platform Architecture](../architecture/platform-architecture.md) — System overview
- [PartMetadata v1](../architecture/partmetadata-v1.md) — Metadata schema
