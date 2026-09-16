# RepoLens

**Understand the code before you change it.**

RepoLens is a developer tool that analyzes real source repositories to help you:

1. **Find** — locate where functionality is implemented in unfamiliar code
2. **Investigate** — follow real calls, imports, and references with source-level evidence
3. **Simulate** — trace how a change to one symbol could ripple through the codebase
4. **Review** — get an actionable checklist of what to inspect before merging

> RepoLens analyzes **real repositories** with **real processing**. Every
> relationship you see is extracted from actual parsed source code via
> tree-sitter — never hardcoded, never fabricated.

---

## Features

- **Repository Ingestion** — clone any public GitHub repo, discover files, store metadata
- **Symbol Extraction** — tree-sitter AST parsing for Python, JavaScript, TypeScript (including JSX/TSX)
- **Relationship Engine** — deterministic code graph: imports, calls, references, inheritance, test couplings
- **Search + Investigation** — symbol/file/text search with depth-1 neighborhood exploration
- **Git Diff Analysis** — compare revisions, see what changed with per-line symbol mapping
- **Impact Simulation** — BFS traversal of relationship graph to find potentially affected code
- **Change Review** — deterministic pre-merge checklist with priority and evidence
- **Lens AI** (optional) — explains findings using OpenAI-compatible API; never decides content

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
      ai/                 optional Lens LLM layer
      relationships/      relationship engine (imports, calls, references, inheritance, tests)
      diff/               git diff analysis
      impact/             change impact simulation (BFS)
      review/             pre-merge review workflow
    repositories/         safe repository ingestion (clone, discovery, cleanup)
  tests/                  pytest suite (249 tests)
```

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

- Backend: http://localhost:8000/api/health · docs at /docs
- Frontend: http://localhost:5173

## Testing

```bash
cd backend && .\.venv\Scripts\python -m pytest          # fast (offline) suite
cd backend && .\.venv\Scripts\python -m pytest -m network  # real-clone tests
```

## Performance (Verified on Real Repos)

| Operation | Repository | Result |
|-----------|-----------|--------|
| Parse 236 files | pallets/flask | Python, <5s |
| Build relationship graph | flask | 7,245 edges, ~11s |
| Symbol search | flask `Blueprint` | 10 hits, ~10ms |
| Impact simulation | flask diff | 351 nodes, <0.1s |
| Change review | flask diff | 31 items, <1s |

## Security

Repository contents are treated as untrusted input. RepoLens **never executes repository code**:

- URL validation before any network or git operation
- `git` runs as subprocess with argument arrays (never `shell=True`), timeout, and size caps
- All file paths stored as relative paths; path traversal rejected
- Error responses expose user-safe messages only

See [SECURITY.md](./SECURITY.md) and [PHASE9_REPORT.md](./docs/development/PHASE9_REPORT.md) for details.

## Supported Languages

Python, JavaScript, TypeScript (including JSX/TSX). Parser layer designed for easy extension.

## Development Notes

- Phases 0–10 fully implemented and hardened
- 249 backend tests passing (2 skipped, 4 deselected, 1 warning)
- Frontend typecheck clean, production build succeeds
- See [docs/development/](./docs/development/) for detailed phase reports

## License

Private portfolio project.
