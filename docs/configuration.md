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
| `IDLRAG_IDL_EXECUTABLE` | Licensed command-line IDL/ENVI batch executable used for user-triggered `.pro` artifact runs. The default is `idl`; Workbench/ENVI GUI launchers (`idlde`, `envi_idl`) and `idlrt` are rejected for `.pro` execution. |
| `IDLRAG_IDL_RUN_TIMEOUT_SECONDS` | Timeout for one local IDL run. |
| `IDLRAG_IDL_RUN_MAX_STDOUT_CHARS` | Maximum stdout characters returned from one IDL run. |
| `IDLRAG_IDL_RUN_MAX_STDERR_CHARS` | Maximum stderr characters returned from one IDL run. |
| `IDLRAG_IDL_RUN_MAX_OUTPUT_FILES` | Maximum previewable output files collected from one IDL run directory. |
| `IDLRAG_IDL_RUN_MAX_OUTPUT_FILE_MB` | Maximum size for one collected IDL output file. |
| `IDLRAG_IDL_RUN_ALLOWED_OUTPUT_SUFFIXES` | Comma-separated image suffixes collected from IDL run output. |
| `IDLRAG_IDL_RUN_ALLOWED_INPUT_SUFFIXES` | Comma-separated data suffixes that may be staged into an IDL run as inputs. |
| `IDLRAG_IDL_RUN_MAX_INPUT_FILES` | Maximum number of input artifacts staged into one IDL run. |
| `IDLRAG_IDL_RUN_MAX_INPUT_FILE_MB` | Maximum size for one staged IDL input artifact. |
| `IDLRAG_GEE_ENABLED` | Enables Google Earth Engine data acquisition routes. Keep false until local credentials are configured. |
| `IDLRAG_GEE_PROJECT` | Google Cloud project used for Earth Engine initialization. |
| `IDLRAG_GEE_AUTH_MODE` | GEE auth mode: `service_account` or `adc`. |
| `IDLRAG_GEE_SERVICE_ACCOUNT_EMAIL` | Service account email for GEE service-account auth. Do not commit real values. |
| `IDLRAG_GEE_SERVICE_ACCOUNT_KEY_JSON` | Service account key JSON for GEE service-account auth. Do not commit real values. |
| `IDLRAG_GEE_ALLOWED_DATASETS` | Comma-separated allowlist of GEE datasets exposed to the structured fetch form. |
| `IDLRAG_GEE_MAX_DOWNLOAD_MB` | Maximum direct-download result size for one GEE fetch. |
| `IDLRAG_GEE_DOWNLOAD_TIMEOUT_SECONDS` | Timeout for one GEE direct-download request. |
| `IDLRAG_GEE_MAX_BBOX_DEGREES` | Maximum width/height in degrees for one GEE bbox request. |
| `IDLRAG_GEE_MAX_BANDS` | Maximum number of selected bands in one GEE request. |
| `IDLRAG_GEE_MIN_SCALE` | Minimum allowed GEE download scale. |
| `IDLRAG_GEE_MAX_SCALE` | Maximum allowed GEE download scale. |
| `IDLRAG_RESEARCH_STAC_SEARCH_TIMEOUT_SECONDS` | Timeout for one public STAC metadata search. |
| `IDLRAG_RESEARCH_STAC_SEARCH_MAX_RESULTS` | Maximum STAC candidates retained from one search. |
| `IDLRAG_RESEARCH_STAC_ALLOWED_HOSTS` | Comma-separated HTTPS host allowlist for STAC endpoints and assets; defaults to the two configured STAC services plus `*.blob.core.windows.net` for Planetary Computer objects. |
| `IDLRAG_RESEARCH_STAC_DOWNLOAD_TIMEOUT_SECONDS` | Timeout for one public STAC object download. |
| `IDLRAG_RESEARCH_STAC_MAX_DOWNLOAD_MB` | Maximum bytes downloaded before local crop/validation. Keep bounded; default is 100 MB. |
| `IDLRAG_RESEARCH_STAC_MAX_OUTPUT_PIXELS` | Maximum pixels across all bands after crop/resample. |
| `IDLRAG_RESEARCH_STAC_RANGE_CACHE_MB` | GDAL cache budget for COG HTTP Range reads. |
| `IDLRAG_SEMANTIC_SCHOLAR_API_KEY` | Optional Semantic Scholar API key. Sent only as the `x-api-key` request header; never put it in query parameters, audit candidates, or generated RAG Markdown. |
| `IDLRAG_RESEARCH_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS` | Minimum interval between in-process Semantic Scholar requests. Defaults to `0.25`; keep it bounded to respect public API rate limits. |
| `IDLRAG_INDEX_WORKER_ENABLED` | Enables the in-process document index worker. Keep `true` for local development; Docker Compose sets it to `false` on the API and runs `app.index_worker` as a separate service. |
| `IDLRAG_INDEX_WORKER_HEARTBEAT_TIMEOUT_SECONDS` | Maximum age of an external worker heartbeat before Dashboard reports it as stale. Defaults to 30 seconds. |
| `IDLRAG_RESEARCH_RUN_TIMEOUT_MINUTES` | Operational timeout for a research run left in `running` after a worker/process interruption. The worker marks such runs `failed`, or `cancelled` when a cancellation request was already recorded, instead of leaving them permanently active; defaults to 120 minutes. |
| `IDLRAG_DEFAULT_PROVIDER_NAME` | Default provider label shown in settings. |
| `IDLRAG_DEFAULT_API_BASE_URL` | Default OpenAI-compatible API base URL. |
| runtime `api_base_url` / `provider_name` | The Agent accepts keyless local OpenAI-compatible endpoints only when the host is `localhost`, `127.0.0.1`, `::1`, `host.docker.internal`, or the provider is explicitly `local`/`ollama`/`lmstudio`; public endpoints still require an API key. |
| `IDLRAG_DEFAULT_CHAT_MODEL` | Default chat model name. |
| `IDLRAG_DEFAULT_EMBEDDING_MODEL` | Default embedding model name. |
| `IDLRAG_EMBEDDING_DIMENSIONS` | Embedding vector dimensions used for indexing. |

Runtime model settings can separate the two OpenAI-compatible channels:

| Setting | Purpose |
|---|---|
| `api_base_url` / `api_key` / `chat_model` | Chat and Agent generation. The local setup uses Gemini2API here. |
| `embedding_api_base_url` / `embedding_api_key` / `embedding_model` | Document/query embeddings. Leave the base URL and key empty to reuse the chat channel; configure them separately for a local embedding server. |

The Settings page provides independent connection checks. A successful chat check does not imply that `/embeddings` is available. If the embedding check fails, the default hybrid retrieval protects quality by using FTS + rule ranking; `vector_only` remains a diagnostic strategy.

## Frontend variables

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | Full backend API prefix, for example `http://127.0.0.1:8000/api`. |

The frontend reads this value in `frontend/src/api/client.ts`.

## Docker / reverse-proxy SSE

The Compose web container serves the frontend and proxies `/api/` to FastAPI.
The proxy configuration in `frontend/nginx.conf` deliberately disables response
buffering and caching, uses HTTP/1.1, clears the hop-by-hop `Connection` header,
and allows long-running Agent/tool turns to stay open for up to one hour. These
settings are required for `ask-stream` and `agent-stream`; without them, a
reverse proxy may hold all tokens until the model finishes or close a slow
research run before its terminal `done`/`error` event arrives.

If the frontend is placed behind another ingress or reverse proxy, carry the
same behavior over to that layer:

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
proxy_set_header Connection "";
add_header X-Accel-Buffering no always;
```

Do not use a short 60-second idle timeout for Agent requests: the backend emits
bounded `waiting` SSE events during slow Gemini2API/tool turns, but the proxy
still needs a timeout longer than the maximum expected turn.

## Local IDL execution

`IDLRAG_IDL_EXECUTABLE` should point to the licensed command-line IDL interpreter when using Windows IDL 8.8:

```text
IDLRAG_IDL_EXECUTABLE=D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\idl.exe
```

The backend executes generated Chat `.pro` artifacts with the configured executable:

```text
idl.exe -batch <run_dir>/__idlrag_runner.pro
```

The same setting is used by the project-level Research IDLRunner. In this
workspace, `envi_idl.exe` starts and exits as a Workbench launcher but does not
produce the declared GeoTIFF; `idlde.exe` behaved similarly in a headless
probe, while `idlrt.exe` is a SAV-only runtime. The runner rejects all three
names before launching. The candidate `idl.exe` reaches the IDL process but the
strict project probe currently returns `Failed to initialize IDL instance` and
does not create `idl_probe.tif`, so the Run is reported as `unavailable` rather
than accepted. Keep the `idl.exe` path explicit in `.env`, initialize the
required ENVI batch runtime inside the `.pro` procedure when ENVI APIs are
used, and verify the local license before enabling formal IDL experiments.

The opt-in local acceptance probe can be repeated with:

```powershell
$env:IDLRAG_REAL_IDL_EXECUTABLE='D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\idl.exe'
uv run --project backend pytest backend/tests/test_research_idl_runner.py -k real_local_probe -q
```

For each run, the backend creates:

```text
data/generated/chat/user-{user_id}/session-{session_id}/runs/{run_id}/
  source.pro
  __idlrag_runner.pro
  stdout.log
  stderr.log
  inputs/
  outputs/
```

Only image files written under `outputs/` with suffixes from `IDLRAG_IDL_RUN_ALLOWED_OUTPUT_SUFFIXES` are returned to the frontend as previewable `idl_output` artifacts. Selected GEE/data artifacts are copied into `inputs/` before execution and should be referenced from generated IDL code as `../inputs/<file>`. The route never accepts arbitrary shell commands or arbitrary local file paths.

## Google Earth Engine data acquisition

GEE support is disabled by default. When enabled, Chat can fetch small bounded datasets through structured parameters and save them under:

```text
data/generated/chat/user-{user_id}/session-{session_id}/gee/{artifact_id}/
```

The first version uses direct downloads for small rasters. It does not accept arbitrary Earth Engine Python/JavaScript code and does not run long-lived Drive or Cloud Storage export jobs. Use `IDLRAG_GEE_ALLOWED_DATASETS`, bbox limits, band limits, scale limits, timeouts and download-size limits to keep the integration bounded.

### Prerequisites

1. Use a Google account that has access to Google Earth Engine.
2. Create or choose a Google Cloud Project.
3. Enable the Google Earth Engine API for that project in Google Cloud Console.
4. Keep the Project ID, not the numeric project number. It is used by `ee.Initialize(project=...)`.

If the API is not enabled, Earth Engine initialization fails with a message similar to:

```text
Google Earth Engine API has not been used in project <project-id> before or it is disabled.
```

### Local ADC authentication

For local development, ADC is usually the simplest option because it uses a browser login and stores the local Earth Engine token outside the repository.

Authenticate once:

```powershell
uv run --project backend python -c "import ee; ee.Authenticate(auth_mode='localhost')"
```

Then set:

```text
IDLRAG_GEE_ENABLED=true
IDLRAG_GEE_AUTH_MODE=adc
IDLRAG_GEE_PROJECT=your-google-cloud-project-id
```

Do not commit the generated local credential files. They are user-local auth state, not project source code.

### Service account authentication

Use service-account auth for a controlled backend deployment. The service account must have access to the Earth Engine-enabled project.

```text
IDLRAG_GEE_ENABLED=true
IDLRAG_GEE_AUTH_MODE=service_account
IDLRAG_GEE_PROJECT=your-google-cloud-project-id
IDLRAG_GEE_SERVICE_ACCOUNT_EMAIL=service-account-name@project-id.iam.gserviceaccount.com
IDLRAG_GEE_SERVICE_ACCOUNT_KEY_JSON={...}
```

Never commit `IDLRAG_GEE_SERVICE_ACCOUNT_KEY_JSON`, key files, token files, or downloaded GEE artifacts.

## Public STAC research data

The Research page can search the configured public STAC providers without sending private project data. A candidate may be registered as a remote `reference`, or explicitly downloaded into the private `research://assets/` store. Planetary Computer Azure Blob assets are authorized through its public SAS service; the short-lived SAS URL is used only for the download and is not persisted in the project database. Downloads are streamed with a size and timeout limit, validated as GeoTIFF/COG, optionally cropped from a WGS84 bbox, optionally resampled, hashed, and only then made eligible for a DataSnapshot and PythonRunner. `target_resolution` is expressed in metres at the API boundary; geographic CRS windows are converted from degrees using their latitude-dependent metre scale, while projected CRS windows use their CRS linear-unit factor. When a remote object is larger than the full-download limit but is a range-readable COG and a crop bbox is supplied, Rasterio/GDAL reads only the requested window; in that mode the DataAsset SHA-256 covers the stored crop output rather than the unmaterialized full source object.

For production deployments, review `IDLRAG_RESEARCH_STAC_ALLOWED_HOSTS` and keep the list narrow. The default `*.blob.core.windows.net` entry is needed for Planetary Computer assets but should not be expanded to arbitrary schemes or domains.

### Smoke test

After authentication and project configuration, test a small SRTM download before using the Chat UI:

```powershell
$env:PYTHONPATH='backend'
$env:IDLRAG_GEE_ENABLED='true'
$env:IDLRAG_GEE_AUTH_MODE='adc'
$env:IDLRAG_GEE_PROJECT='your-google-cloud-project-id'
uv run --project backend python -c "import ee, httpx; ee.Initialize(project='$env:IDLRAG_GEE_PROJECT'); region=ee.Geometry.Rectangle([116.30,39.85,116.31,39.86]); image=ee.Image('CGIAR/SRTM90_V4').select(['elevation']).clip(region); url=image.getDownloadURL({'name':'idlrag_gee_smoke','scale':90,'crs':'EPSG:4326','region':region,'format':'GEO_TIFF'}); r=httpx.get(url, timeout=120, follow_redirects=True); r.raise_for_status(); print({'bytes': len(r.content), 'content_type': r.headers.get('content-type')})"
```

A successful result prints a non-zero byte count. In the Chat UI, the expected flow is:

1. Click the GEE data button in Chat.
2. Choose an allowed dataset, bbox, bands, scale and CRS.
3. Fetch data; the result becomes a `gee_data` artifact in the current session.
4. Use the artifact as an IDL input.
5. Generate a `.pro` file from retrieved documentation and the selected input artifact.
6. Click “运行 IDL”; the backend stages the GEE file under `runs/{run_id}/inputs/` and collects images from `outputs/`.

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
IDLRAG_IDL_EXECUTABLE=idl
IDLRAG_IDL_RUN_TIMEOUT_SECONDS=30
VITE_API_BASE_URL=http://127.0.0.1:8000/api
```

## Production notes

- Use a strong unique `IDLRAG_AUTH_SECRET`.
- Do not allow arbitrary import paths unless the deployment is fully trusted.
- Back up `data/` securely if the instance contains important user data.
- Do not use GitHub as a backup for runtime data.
- Rebuild document indexes after changing embedding model or dimensions.
