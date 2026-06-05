# Demo Guide

This guide describes a repeatable local demo for IDL RAG Panel.

## 1. Start services

Backend:

```powershell
uv sync --project backend
uv run --project backend uvicorn app.main:app --app-dir backend --reload
```

Frontend:

```powershell
npm install --prefix frontend
npm run dev --prefix frontend
```

Open:

```text
http://127.0.0.1:5173
```

## 2. Sign in

Use an existing local account or register a new one. For public demos, do not show real passwords, API keys or private documents on screen.

## 3. Configure provider

Open Settings and configure:

- Provider name.
- API base URL.
- API key.
- Chat model.
- Embedding model.
- Optional rerank and LangSmith settings.

Use the connection test before importing a large knowledge base.

## 4. Create a knowledge base

Open KnowledgeBases:

1. Create a knowledge base with a clear name.
2. Keep the default retrieval strategy as `hybrid_rrf_no_rerank` unless you are deliberately comparing strategies.
3. Keep `top_k` small for a quick demo, for example 6.

## 5. Import documents

Open Documents:

1. Upload a small reviewed `.pdf`, `.md`, `.txt`, `.pro` or `.idl` file.
2. Wait for status to become `ready`.
3. If a document is `failed`, open the error, fix the source/config issue, and retry.
4. If embedding provider changed, rebuild index.

For GitHub demos, use only public or manually reviewed sample material.

## 6. Chat demo

Open Chat:

1. Select the knowledge base.
2. Ask a domain question, for example an ENVI/IDL usage question tied to the imported document.
3. Confirm the response streams normally.
4. Inspect citations and check strategy/score/metadata.
5. Toggle `.pro` generation if the demo includes code generation.
6. Click `运行 IDL` on a generated `.pro` artifact.
7. Confirm the IDL run summary card shows status, exit code, duration, and output image count.
8. Open the generated image preview and download the artifact if needed.

Expected result:

- The answer is grounded in retrieved content.
- Citation cards are visible.
- Retrieval policy is shown.
- IDL run summary cards are readable when `.pro` execution is demonstrated.
- Generated output images are visible as thumbnails and open in the preview drawer.
- No transparent duplicated selector boxes or floating UI artifacts appear.

## 7. RetrievalLab demo

Open RetrievalLab:

1. Select the same knowledge base.
2. Run the same query with `hybrid_rrf_no_rerank`.
3. Compare with `hybrid_rrf` if rerank is configured.
4. Use `vector_only` only to diagnose embedding quality.
5. Open a candidate drawer and inspect scores, match info and metadata.

Expected result:

- Candidate chunks are visible.
- Strategy and top_k are clear.
- Metadata/raw JSON can be inspected without overwhelming the main page.

## 8. Evaluation demo

Open Settings:

1. Run a small local evaluation if a suitable test dataset exists.
2. Open the report detail drawer.
3. Compare pass rate, hit rate, precision, recall, MRR and latency by strategy.
4. Explain that code-tool-mode cases are evaluated separately from natural-language QA.

## 9. Verification commands

Run focused backend tests:

```powershell
uv run --project backend pytest backend/tests/test_retrieval_strategies.py backend/tests/test_agent_service.py
uv run --project backend pytest backend/tests/test_eval_golden_qa.py backend/tests/test_eval_metrics.py backend/tests/test_evaluation_api.py
```

Build frontend:

```powershell
npm run build --prefix frontend
```

## 10. Pre-publish check

Before committing or pushing:

```powershell
git status
git diff --cached
```

Confirm these are not staged:

- `.env` or real provider keys.
- `data/app.db`, WAL/SHM, indexes, logs, parsed text or generated artifacts.
- Private PDFs, resume drafts, course materials or unreviewed source documents.
- `frontend/node_modules/`, `frontend/dist/`, `backend/.venv/` or caches.
