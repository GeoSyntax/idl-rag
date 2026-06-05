# Architecture

This document provides the maintainable architecture view for IDL RAG Panel. The shorter public overview is in [`../README.md`](../README.md), and the full module map is in [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## System boundary

```mermaid
flowchart LR
  Browser[Browser] --> Frontend[React workbench]
  Frontend --> Backend[FastAPI API]
  Backend --> SQLite[(SQLite app.db)]
  Backend --> FTS[(SQLite FTS5)]
  Backend --> Lance[(LanceDB)]
  Backend --> RuntimeFiles[Local runtime files]
  Backend --> Providers[OpenAI-compatible providers]
```

The GitHub repository should contain source code, tests, documentation and configuration templates. Runtime storage under `data/` is outside the publishable boundary.

## Runtime components

| Component | Role |
|---|---|
| React frontend | User-facing workbench for dashboard, knowledge bases, documents, chat, retrieval debugging and settings. |
| FastAPI backend | Authenticated API, index worker lifecycle, RAG orchestration, evaluation and settings. |
| SQLite | Users, settings, knowledge bases, documents, chunks, jobs, chat sessions, reports and metadata. |
| SQLite FTS5 | Keyword retrieval over indexed chunks. |
| LanceDB | Vector index for semantic retrieval. |
| Local files | Uploaded sources, parsed text, generated artifacts and logs. |
| OpenAI-compatible providers | Chat, embedding and optional rerank endpoints. |

## Query lifecycle

```mermaid
sequenceDiagram
  participant U as User
  participant F as Frontend
  participant A as FastAPI
  participant R as RetrievalService
  participant S as Storage
  participant L as LLM provider

  U->>F: Ask question
  F->>A: POST /api/chat/ask-stream
  A->>A: Authenticate and persist user message
  A->>R: Resolve strategy and retrieve citations
  R->>S: Read FTS, vectors and chunk metadata
  S-->>R: Candidate chunks
  R-->>A: Ranked citations
  A->>L: Grounded prompt with citations
  L-->>A: Streamed tokens
  A-->>F: SSE-style stream events
  A->>S: Persist assistant message and artifacts
```

## Index lifecycle

```mermaid
sequenceDiagram
  participant U as User
  participant F as Frontend
  participant A as FastAPI
  participant W as Index worker
  participant S as Storage
  participant E as Embedding provider

  U->>F: Upload/import document
  F->>A: POST document import API
  A->>S: Create document and queued index job
  W->>S: Claim queued job
  W->>W: Extract text and chunk document
  W->>S: Write chunks and FTS rows
  W->>E: Request embeddings
  E-->>W: Embeddings or failure
  W->>S: Write vectors or hash fallback vectors
  W->>S: Mark document ready or failed
```

## Retrieval strategy notes

- `hybrid_rrf_no_rerank` is the current default because it performed close to rerank-enabled hybrid retrieval with lower latency in the local benchmark.
- `hybrid_rrf` remains available as the rerank comparison strategy.
- `multi_query` is useful for complex questions but is slower.
- `vector_only` is a diagnostic strategy for embedding/index quality.
- Code tools such as `symbol_search`, `read_context`, `find_callers` and `find_callees` should be evaluated with code-tool cases, not generic QA questions.

## Security model

- Passwords are stored as PBKDF2-SHA256 hashes.
- Runtime provider keys are encrypted in SQLite through `backend/app/core/security.py`.
- Encryption does not make `data/app.db` safe to publish; it remains local runtime state.
- The publishable repository excludes real `.env` files, local DBs, indexes, logs, private documents and generated artifacts.
