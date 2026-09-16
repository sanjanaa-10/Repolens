# RepoLens — Project Summary

**"Understand the code before you change it."**

---

## Problem

When working with unfamiliar codebases, developers face a fundamental gap: they
cannot efficiently answer "what depends on this?", "what would this change
affect?", or "what should I review before merging?" Existing tools show dashboards
or dependency graphs, but they don't provide evidence-backed answers grounded in
real parsed source code.

## Solution

RepoLens is a developer investigation tool that analyzes real source repositories
and answers those questions with source-level evidence. It performs deterministic,
offline static analysis — never fabricates relationships, never requires code
execution, and never depends on an LLM to produce its findings.

The core workflow:

```
INGEST → PARSE → INVESTIGATE → DIFF → IMPACT → REVIEW
```

Every symbol, relationship, diff hunk, impact path, and review item is generated
from actual parsed source code via tree-sitter, with exact file + line evidence.

---

## Architecture

```
Frontend (React + TypeScript + Vite + Tailwind)
    ↓
FastAPI API (33 endpoints)
    ↓
┌─────────────────────────────────────────────────┐
│  Ingestion    │  safe clone, file discovery      │
│  Parsing      │  tree-sitter AST (Python/JS/TS)  │
│  Relationships│  7 edge types, resolution status │
│  Search       │  symbol/file/text, deterministic │
│  Diff         │  real git subprocess execution   │
│  Impact       │  BFS over relationship graph     │
│  Review       │  deterministic checklist generator│
│  Lens (opt.)  │  bounded LLM explanation layer   │
└─────────────────────────────────────────────────┘
    ↓
SQLite (all state, all queries parameterized)
```

Lens is optional. Without a provider key, the deterministic core is fully
functional. When configured, Lens explains evidence the deterministic layers
already proved — it never creates or decides findings.

---

## Technical Highlights

- **Real repository analysis.** Ingest any public GitHub repository. Every
  relationship is extracted from actual parsed source — never hardcoded.
- **7 relationship edge types** with resolution status (RESOLVED / UNRESOLVED /
  EXTERNAL) and per-edge source evidence.
- **Deterministic impact simulation.** BFS traversal of the relationship graph,
  cycle-safe, bounded, with explainable dependency paths.
- **Deterministic review generation.** Byte-deterministic: identical inputs always
  produce identical checklists. Priority, grouping, and evidence are all derived
  from verified findings.
- **Security-hardened.** Centralized safe Git execution, path containment,
  enforced foreign keys, resource caps, trusted hosts, request-ID correlation,
  shared secret redaction, bounded local concurrency.
- **Optional AI layer.** OpenAI-compatible LLM explanations over bounded,
  evidence-selected context. Metadata-only audit logging. No prompts, snippets,
  or responses stored.

---

## Security Highlights

- Repository code is **data** — never executed, never rendered as HTML, never
  treated as instructions by the LLM.
- All file reads use a single `safe_join` enforcement point.
- Git subprocesses run with argument arrays only, hardened environment, mandatory
  `-c` overrides, 64 MiB output cap, and timeouts.
- `PRAGMA foreign_keys=ON` per session; full cascade deletion.
- LLM response capped at 1 MiB; fabricated evidence indices rejected server-side.
- 41-item security checklist (Phase 9) — all complete.

---

## Verified Metrics

| Surface | Result |
| --- | --- |
| Real repos ingested | 4 (flask, itsdangerous, requests, urllib3) |
| Total files | 599 |
| Flask relationships | 7,245 edges |
| itsdangerous relationships | 1,080 edges |
| Real diff (flask) | 8 files, 19 changed symbols, 97 potential impact nodes |
| Review (flask) | 31 items, 24 required, ~70 ms generation |
| Backend tests | 249 passed / 2 skipped / 4 deselected / 1 warning |
| Frontend | TypeScript clean, Vite build passes |
| Security tests | 16 hardening tests passing |
| Dependency audit | 0 Python vulns, 7 JS findings documented |

---

## Limitations

- **Static, syntax-level only.** Runtime-only couplings, dynamic `getattr`, and
  C extensions are not reachable. `UNRESOLVED` is reported, not guessed.
- **Public GitHub repositories only.** No private repos, no other hosting
  providers.
- **Python, JavaScript, TypeScript.** Additional languages can be added via
  tree-sitter.
- **Single-user, local.** No authentication, no collaboration, no cloud.
- **Lens is advisory.** LLM explanations may be imperfect; the deterministic
  analysis is always the source of truth.
- **Frontend dependency advisories.** react-router-dom and prismjs have known
  issues (documented in docs/development/PHASE9_REPORT.md); fixes require breaking major upgrades.

---

## How to Demo

See [DEMO.md](./DEMO.md) for a 3–5 minute step-by-step walkthrough.

---

## Resume-Ready Description

RepoLens is a full-stack developer investigation tool (Python/FastAPI backend +
React/TypeScript frontend) that analyzes real source repositories through
deterministic static analysis: AST parsing via tree-sitter, a 7-type relationship
engine with source-level evidence, real git diff analysis, BFS impact simulation
over the relationship graph, deterministic change review generation, and an
optional LLM explanation layer. The project spans 11 phases from architecture
through security hardening and deployment, with 249 passing backend tests (2 skipped, 4 deselected, 1 warning) and production
deployment documentation.