# Configuration

IDL RAG Panel uses environment variables for boot-time defaults and the Settings page for runtime provider configuration.

## Environment template

Start from the public template:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`.

## Backend variables

All backend variables use the `IDLRAG_` prefix and are loaded by `backend/app/core/config.py`.

| Variable | Purpose |
|---|---|
| `IDLRAG_ENVIRONMENT` | `development`, `production`, or another deployment label. |
| `IDLRAG_BASE_DIR` | Runtime data directory. Defaults to project `data/`. |
| `IDLRAG_AUTH_SECRET` | Token signing and secret-encryption root. Must be changed outside local experiments. |
| `IDLRAG_PREVIOUS_AUTH_SECRETS` | Comma-separated old secrets for decrypting rotated encrypted settings. |
| `IDLRAG_CORS_ORIGINS` | Comma-separated frontend origins allowed by the API. |
| `IDLRAG_IMPORT_ROOTS` | Comma-separated directories allowed for path import. |
| `IDLRAG_ALLOW_ARBITRARY_IMPORT_PATH` | Enables unrestricted local path import. Keep false unless fully trusted. |
| `IDLRAG_MAX_UPLOAD_FILES` | Max files per upload request. |
| `IDLRAG_MAX_UPLOAD_FILE_MB` | Max single file size. |
| `IDLRAG_MAX_UPLOAD_TOTAL_MB` | Max total upload size. |
| `IDLRAG_MAX_PDF_PAGES` | PDF page limit. |
| `IDLRAG_IDL_EXECUTABLE` | Local IDL Workbench command used for user-triggered `.pro` artifact runs. Defaults to `idlde` and is called with `-batch`. |
| `IDLRAG_IDL_RUN_TIMEOUT_SECONDS` | Timeout for one local IDL run. |
| `IDLRAG_IDL_RUN_MAX_STDOUT_CHARS` | Maximum stdout characters returned from one IDL run. |
| `IDLRAG_IDL_RUN_MAX_STDERR_CHARS` | Maximum stderr characters returned from one IDL run. |
| `IDLRAG_IDL_RUN_MAX_OUTPUT_FILES` | Maximum previewable output files collected from one IDL run directory. |
| `IDLRAG_IDL_RUN_MAX_OUTPUT_FILE_MB` | Maximum size for one collected IDL output file. |
| `IDLRAG_IDL_RUN_ALLOWED_OUTPUT_SUFFIXES` | Comma-separated image suffixes collected from IDL run output. |
| `IDLRAG_DEFAULT_PROVIDER_NAME` | Default provider label shown in settings. |
| `IDLRAG_DEFAULT_API_BASE_URL` | Default OpenAI-compatible API base URL. |
| `IDLRAG_DEFAULT_CHAT_MODEL` | Default chat model name. |
| `IDLRAG_DEFAULT_EMBEDDING_MODEL` | Default embedding model name. |
| `IDLRAG_EMBEDDING_DIMENSIONS` | Embedding vector dimensions used for indexing. |

## Frontend variables

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | Full backend API prefix, for example `http://127.0.0.1:8000/api`. |

The frontend reads this value in `frontend/src/api/client.ts`.

## Local IDL execution

`IDLRAG_IDL_EXECUTABLE` should point to the IDL Workbench launcher when using Windows IDL 8.8:

```text
IDLRAG_IDL_EXECUTABLE=D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\idlde.exe
```

The backend executes generated Chat `.pro` artifacts with:

```text
idlde.exe -batch <run_dir>/__idlrag_runner.pro
```

For each run, the backend creates:

```text
data/generated/chat/user-{user_id}/session-{session_id}/runs/{run_id}/
  source.pro
  __idlrag_runner.pro
  stdout.log
  stderr.log
  outputs/
```

Only image files written under `outputs/` with suffixes from `IDLRAG_IDL_RUN_ALLOWED_OUTPUT_SUFFIXES` are returned to the frontend as previewable `idl_output` artifacts. The route never accepts arbitrary shell commands or arbitrary local file paths.

## Runtime provider settings

The Settings page stores provider values through `backend/app/services/settings_service.py`.

Sensitive runtime keys:

- `api_key`
- `rerank_api_key`
- `langsmith_api_key`

These values are encrypted with helpers from `backend/app/core/security.py` before they are stored in SQLite. The API returns boolean flags such as `has_api_key` instead of echoing secrets back to the frontend.

## Key rotation

To rotate `IDLRAG_AUTH_SECRET` without losing access to encrypted runtime settings:

1. Set the new value in `IDLRAG_AUTH_SECRET`.
2. Put the old value in `IDLRAG_PREVIOUS_AUTH_SECRETS`.
3. Start the app and open Settings so stored secrets can be decrypted with the old key and re-encrypted with the new key.
4. After migration, remove the old value from `IDLRAG_PREVIOUS_AUTH_SECRETS`.

Do not upload either the current or previous secrets.

## Local development example

```text
IDLRAG_ENVIRONMENT=development
IDLRAG_BASE_DIR=./data
IDLRAG_AUTH_SECRET=replace-with-a-long-random-secret
IDLRAG_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
IDLRAG_IDL_EXECUTABLE=idlde
IDLRAG_IDL_RUN_TIMEOUT_SECONDS=30
VITE_API_BASE_URL=http://127.0.0.1:8000/api
```

## Production notes

- Use a strong unique `IDLRAG_AUTH_SECRET`.
- Do not allow arbitrary import paths unless the deployment is fully trusted.
- Back up `data/` securely if the instance contains important user data.
- Do not use GitHub as a backup for runtime data.
- Rebuild document indexes after changing embedding model or dimensions.
