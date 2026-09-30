# IDL RAG Panel

Language: [中文](./README.md) | **English**

**A reproducible workspace for remote-sensing research, from sources to runnable experiments**

Upload papers, remote-sensing notes, IDL code, or private data. Ask the Agent, generate IDL/Python experiments, run a preview locally, and inspect citations, logs, and stage images.

<p align="center">
  <img src="./docs/assets/demos/idl-rag-panel-demo-poster.png" alt="IDL RAG Panel preview" width="900" />
</p>

## Demo

The video shows the platform flow: open the workspace, manage a knowledge base, use the Agent, inspect retrieval evidence, run a script, and review the returned result.

<video controls muted loop playsinline poster="./docs/assets/demos/idl-rag-panel-demo-poster.png" width="900">
  <source src="./docs/assets/demos/idl-rag-panel-demo.mp4" type="video/mp4">
</video>

[Open or download the platform demo](./docs/assets/demos/idl-rag-panel-demo.mp4)

## What you can do

- **Index research material:** Search papers, remote-sensing documents, IDL `.pro` files, and Python code from one workspace.
- **Ask with evidence:** Return citations, code context, and retrieval locations for review.
- **Generate experiments:** Turn a research question and selected sources into IDL or Python code without a fixed template.
- **Run and inspect:** Execute a controlled local preview with IDL or Python/GDAL and inspect logs, stage images, and output files.
- **Compare retrieval:** Inspect candidates, scores, strategies, and metadata in Retrieval Lab.
- **Keep data local:** Upload private material and retain project artifacts in local storage.

## One workflow

```text
Research question
  -> retrieve papers, remote-sensing notes, and code
  -> confirm data and formula
  -> generate Python / IDL
  -> run a user-confirmed preview
  -> inspect stage images, logs, and GeoTIFF
  -> revise the formula or parameters
```

Example output from the MNDWI workflow:

<p align="center">
  <img src="./docs/assets/demos/poyang-mndwi-preview-feature.png" alt="MNDWI feature image" width="45%" />
  <img src="./docs/assets/demos/poyang-mndwi-preview-mask.png" alt="Water mask result" width="45%" />
</p>

The full execution record is in [`docs/agent-demo-result.md`](./docs/agent-demo-result.md).

## Screens

<table>
  <tr>
    <td><img src="./docs/assets/screenshots/dashboard.png" alt="Workspace" /></td>
    <td><img src="./docs/assets/screenshots/chat.png" alt="Agent chat" /></td>
  </tr>
  <tr>
    <td align="center">Workspace status</td>
    <td align="center">Agent-generated script and run result</td>
  </tr>
  <tr>
    <td><img src="./docs/assets/screenshots/knowledge-bases.png" alt="Knowledge bases" /></td>
    <td><img src="./docs/assets/screenshots/retrieval-lab.png" alt="Retrieval Lab" /></td>
  </tr>
  <tr>
    <td align="center">Papers, remote-sensing notes, and IDL code</td>
    <td align="center">Candidates, scores, and code context</td>
  </tr>
</table>

## Install

Requires Python 3.12, Node.js 18+, and `uv`:

```powershell
Copy-Item .env.example .env
uv sync --project backend
npm install --prefix frontend
```

Start the backend and frontend in separate terminals:

```powershell
uv run --project backend uvicorn app.main:app --app-dir backend --reload
```

```powershell
npm run dev --prefix frontend
```

Open <http://127.0.0.1:5173>. For container deployment, run `docker compose up --build`.

## Research boundary

- The platform retrieves sources, generates code, runs controlled previews, and records results. Researchers still review formula validity, sample representativeness, and scientific conclusions.
- IDL is an optional compatibility layer. Python/GDAL covers the main remote-sensing workflow when IDL is unavailable.
- Private files and runtime artifacts stay under local `data/` and are excluded from commits.

## Docs

- [Research workflow](./docs/research-workflow-platform-plan.md)
- [Demo steps](./docs/demo.md)
- [Poyang Lake case](./docs/real-research-case-poyang.md)
- [Configuration](./docs/configuration.md) · [Data control](./docs/security-and-data-control.md)
- [Showcase](./docs/project-showcase.en-US.md)
