# Research workflow backend application

## Overview

`backend/app` is the FastAPI application layer for IDL RAG Panel. It exposes the local-first research workspace used by teachers and students: project protocols, private data assets, literature/STAC/GEE acquisition, project-scoped RAG, controlled Python/IDL execution, validation samples, and evidence packages.

The module is deliberately Python-first. IDL remains an optional, user-triggered compatibility path; a missing IDL license or executable must not prevent Python research runs.

## Main capabilities

- **Project and protocol lifecycle**: open/template projects, protocol revisions, frozen snapshots, readiness checks, and reproducible experiment metadata.
- **Private geospatial assets**: upload and hash local GeoTIFFs, register structured GEE/STAC sources, crop/resample public COGs, and explicitly align multiple raster bands into a new private GeoTIFF.
- **Evidence-backed methods**: EvidenceCards, FormulaSpecs, project-bound Method/IDL/Python knowledge bases, audited Crossref/OpenAlex/Semantic Scholar discovery (including bounded citation/reference expansion), and explicit `abstract_only` literature-record import into a bound Method RAG.
- **Provider safety**: Semantic Scholar can use an optional environment-managed API key and an in-process request interval; 429/timeout/provider failures become retryable 503 responses without persisting secrets.
- **Controlled runners**: PythonRunner operations for optical, SAR, fusion, Otsu, and safe declarative band math; project-level IDLRunner for explicitly uploaded private `.pro` scripts on a licensed local node; stage-by-stage raster/PNG previews. Runs can execute synchronously or enter an audited queue consumed by the embedded/standalone worker; queued runs support cooperative cancellation and independent retry records.
- **Validation and export**: frozen data snapshots, independent sample contracts, spatial/temporal metrics, audit logs, and protected Research Evidence Packages.

## Local development

From the repository root:

```powershell
uv run --project backend uvicorn app.main:app --app-dir backend --reload
```

The application reads `IDLRAG_*` settings. For a disposable test instance, set `IDLRAG_BASE_DIR` to a temporary directory and use the API under `/api`.

## API areas

| Area | Route prefix | Responsibility |
|---|---|---|
| Authentication | `/api/auth` | Registration, login, current-user access. |
| Research projects | `/api/research/projects` | Projects, protocols, assets, snapshots, experiments, runs, validation and evidence. |
| Literature/STAC/GEE | `/api/research/projects/{id}/literature-search`, `/literature-search/semantic-scholar-network`, `/literature-search/rag-import`, `/stac-search`, `/gee-fetch` | Audited external metadata and bounded Semantic Scholar relationship discovery, explicit abstract-only Method RAG import, and explicit data materialization. |
| Project IDLRunner | `/api/research/projects/{id}/idl-scripts/upload` and experiment `runner_type=idl` | Private `.pro` source registration and explicit local licensed-node execution with bounded inputs/outputs. |
| RAG | `/api/research/projects/{id}/rag-sources`, `/rag-search` | Project-scoped bindings and cited text retrieval. |
| Chat/IDL | `/api/chat` | Existing chat RAG and user-triggered local IDL artifacts. |

## Important services

- `services/research_service.py`: project, asset, snapshot, experiment and evidence persistence with object-level access checks.
- `services/research_stac_service.py`: public STAC search, Planetary Computer SAS authorization, full download and COG HTTP Range crop.
- `services/research_raster_stack_service.py`: same-project private raster validation, reference-grid reprojection/resampling and multi-band GeoTIFF creation.
- `services/python_runner.py`: bounded raster operations, manifest generation, visual outputs and validation metrics.
- `services/idl_runtime.py`: shared guard that rejects Workbench/GUI and SAV-only launchers before a project `.pro` run can be misreported as successful.
- `worker_healthcheck.py`: standalone heartbeat check used by Compose and the API readiness contract.
- `services/research_evidence_package.py`: protected reproducibility package assembly without copying private source pixels.

## Verification

```powershell
uv run --project backend pytest -q
uv run --project backend ruff check backend/app backend/tests
```

The repository-level acceptance record is maintained in [`docs/v1-acceptance.md`](../../docs/v1-acceptance.md). This module does not claim a scientific conclusion merely because a preview run succeeds; formal results require a frozen protocol, independent validation and an auditable evidence package.
