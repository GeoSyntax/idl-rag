# Security and Data Control

This document defines what can be committed to GitHub and what must remain local.

## Safe to commit by default

- Source code under `backend/app/` and `frontend/src/`.
- Tests under `backend/tests/`.
- Package and config files such as `backend/pyproject.toml`, frontend package files and TypeScript/Vite config.
- Public documentation: `README.md`, `ARCHITECTURE.md`, `PROJECT_STATUS.md`, `docs/`.
- `.env.example` with placeholders only.
- `.gitignore`.

## Do not commit

### Secrets and local configuration

- `.env`
- `.env.*` except `.env.example`
- Real API keys, rerank keys, LangSmith keys or auth secrets
- `.claude/settings.local.json`
- Local MCP/browser automation state such as `.omc/` and `.playwright-mcp/`

### Runtime application data

- `data/app.db`
- `data/*.db-wal`
- `data/*.db-shm`
- `data/indexes/`
- `data/logs/`
- `data/generated/`
- `data/parsed/`
- `data/cache/`
- `backend/data/`

### Private or unreviewed documents

- Personal PDFs
- Course scans
- Resume drafts
- Imported source documents
- Any data under `data/sources/` or `backend/data/sources/` unless explicitly reviewed and approved for public release

Known local files that should not be uploaded without review:

- `郭浩宇-anget开发.pdf`
- `扫描件_实验一IDL基本运算.pdf`
- `resume_draft.md`

### Dependencies and build output

- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.vite/`
- temporary smoke-test directories

## Why encrypted DB data is still excluded

Runtime provider keys are encrypted before storage, but `data/app.db` can still contain:

- User accounts and roles.
- Chat history.
- Knowledge base metadata.
- Document names and paths.
- Chunks extracted from private documents.
- Evaluation reports.
- Encrypted provider keys.

For that reason the database is treated as private runtime state and is never uploaded to GitHub.

## Publishing checklist

Before each commit:

```powershell
git status
git diff --cached
```

Check that staged files do not include:

- Real secrets.
- Runtime DB/index/log/generated files.
- Private documents.
- Dependency folders.
- Build output.
- Large binary files unless deliberately reviewed.

If unsure about a file, leave it out of the commit.

## Recommended commit order

1. Safety boundary and environment template.
2. Documentation and architecture diagrams.
3. Backend source and tests.
4. Frontend source and build configuration.
5. Public demo material only after manual review.

## GitHub push policy

Do not push until the target GitHub repository URL is confirmed.

Do not force push.

Do not bypass hooks.

If a sensitive file is accidentally staged, unstage it before committing. If a sensitive file is accidentally committed, stop and rotate exposed secrets before publishing.
