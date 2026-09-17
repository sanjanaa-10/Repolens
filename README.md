# RepoLens

**Understand the code before you change it.**

RepoLens is a developer tool that analyzes real source repositories to help you:

1. **Find** — locate where functionality is implemented in unfamiliar code
2. **Investigate** — follow real calls, imports, and references with source-level evidence
3. **Simulate** — trace how a change to one symbol could ripple through the codebase
4. **Review** — get an actionable checklist of what to inspect before merging

Repository code is treated as **data**: RepoLens clones a public repository and analyzes it
with static analysis — it **never executes repository code**.

> Every relationship you see is extracted from real parsed source code via
> tree-sitter — never hardcoded, never fabricated.

---

## Features

- **Repository ingestion** — clone any public GitHub repository, discover files, store metadata
- **Symbol extraction** — tree-sitter AST parsing for Python, JavaScript, TypeScript (including JSX/TSX)
- **Relationship graph** — deterministic code graph: imports, calls, references, inheritance, test couplings
- **Search + investigation** — symbol/file/text search with depth-1 neighborhood exploration
- **Git diff analysis** — compare revisions; see what changed with per-line symbol mapping
- **Impact simulation** — breadth-first traversal of the relationship graph to find potentially affected code
- **Change review** — deterministic pre-merge checklist with priority and evidence
- **Lens** (optional) — explains findings using an OpenAI-compatible API; it never decides content and is optional at runtime

## Architecture

```
frontend/                 React + TypeScript + Vite + Tailwind
backend/                  Python + FastAPI
  app/
    api/                  HTTP layer (FastAPI routers)
    core/                 database + configuration + git_safety + security
    models/               ORM models + Pydantic schemas
    services/             orchestration (ingestion service)
    analysis/
      parsers/            tree-sitter parsers (Python, JS, TS)
      search/             symbol/file/text search + deterministic ranking
      investigation/      depth-1 symbol investigation assembly
      ai/                 optional Lens layer
      relationships/      relationship engine (imports, calls, references, inheritance, tests)
      diff/               git diff analysis
      impact/             change impact simulation (BFS)
      review/             pre-merge review workflow
    repositories/         safe repository ingestion (clone, discovery, cleanup)
  tests/                  pytest suite (249 passed / 2 skipped / 4 deselected / 1 warning)
```

## Verified test results

Run locally: `cd backend && .\.venv\Scripts\python -m pytest`

- **249 passed / 2 skipped / 4 deselected / 1 warning** (offline suite; clone tests require network)
- Frontend: `npm run typecheck` clean and `npm run build` (Vite production build) succeed

## Measurements on real repositories

RepoLens was run against four real public GitHub repositories and completed
ingestion, parsing, and graph building end-to-end:

| Metric | Value |
| --- | --- |
| Repositories analyzed | 4 (pallets/flask, pallets/itsdangerous, psf/requests, urllib3/urllib3) |
| Files parsed | 599 |
| Symbols extracted | 7,276 |
| Relationships built | 35,720 |

Performance on `pallets/flask` (236 files):

| Operation | Result |
| --- | --- |
| Parse | < 5 s |
| Build relationship graph | 7,245 edges, ~11 s |
| Symbol search (Blueprint) | 10 hits, ~10 ms |
| Impact simulation | 351 nodes, < 0.1 s |
| Change review | 31 items, < 1 s |

**Supported vs. observed:** the engine supports seven relationship types; six types were
observed across the analyzed repositories.

## Quick Start

Requirements: Python 3.12, Node 18+

```bash
# Backend
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

- Backend: http://localhost:8000/api/health · docs at `/docs`
- Frontend: http://localhost:5173

Configuration: copy `.env.example` to `backend/.env` and edit.

## Security

Repository contents are treated as untrusted input. RepoLens **never executes repository code**:

- URL validation before any network or git operation
- `git` runs as a subprocess with argument arrays (never `shell=True`), with timeouts and size caps
- All file paths stored as relative paths; path traversal rejected
- Secret-like assignments and header lines are redacted before they reach the AI layer, logs, or errors
- Error responses expose user-safe messages only

See [SECURITY.md](./SECURITY.md) for the full threat model and hardening details.

## Demo

A 3–5 minute walkthrough of the analysis workflow on a real repository:
[DEMO.md](./DEMO.md)

## Deployment

Reference layout, recommended platform, environment variables, backups, and
operational checks: [DEPLOYMENT.md](./DEPLOYMENT.md)

## License

Private portfolio project.