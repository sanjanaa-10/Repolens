# RepoLens Demo

## Objective

Demonstrate the complete deterministic analysis workflow — from ingestion through
review — in approximately 3–5 minutes using a real, publicly accessible GitHub
repository.

**Best demo repository:** `pallets/itsdangerous`
- Small (50 files, ~283 KB) — quick ingestion
- Python only — clean symbol graph
- Has real test files — demonstrates test coupling edges

---

## Prerequisites

- Backend running: `cd backend && uvicorn app.main:app --reload --port 8000`
- Frontend running: `cd frontend && npm run dev`
- Browser: http://localhost:5173

---

## Step-by-step demo

### 1. Ingest a repository (≈ 15s)

1. Open http://localhost:5173 — the landing page shows "UNDERSTAND THE CODE
   BEFORE YOU CHANGE IT."
2. Click **Analyze Repository** (or paste into the input field).
3. Paste `https://github.com/pallets/itsdangerous`.
4. Click Analyze → wait for status to reach **ready**.
5. The overview page shows: ~50 files, ~1,080 relationships, language shares.

**Point out:** URL validation, safe clone (depth 1), real file discovery.

### 2. Parse and build relationships

Parsing and relationship building run automatically or are triggered from the
UI. The overview shows the analysis summary.

**Point out:** tree-sitter AST parsing, 7 edge types, RESOLVED / UNRESOLVED /
EXTERNAL status on every edge.

### 3. Search for a symbol (≈ 5s)

1. Navigate to the **Search** workspace (Investigate page).
2. Search for `Serializer` (symbol mode).
3. Show ranked results — definitions ranked ahead of import aliases.

**Point out:** deterministic ranking, no embeddings, pure string matching.

### 4. Investigate a symbol (≈ 5s)

1. Click on `Serializer` in the results.
2. The investigation panel shows: definition, callers, callees, references,
   dependencies, tests, imports, exports, related files.
3. Click through a few edges to show source-level evidence.

**Point out:** depth-1 neighborhood, every edge carries file + line evidence,
clickable navigation with no dead ends.

### 5. Create a diff (≈ 10s)

1. Navigate to the **Changes** page.
2. Enter two revisions. Use HEAD and HEAD~1, or two known SHAs:
   - Base: `672971d` (or `HEAD~1`)
   - Head: `HEAD`
3. Click Analyze → results show changed files, hunks, changed lines, and
   affected symbols.

**Point out:** real git diff, file status classification, changed-line → symbol
mapping.

### 6. Simulate impact (≈ 5s)

1. From the Changes page, click **Simulate Impact** (or navigate to Impact).
2. Use depth 2 (default).
3. The impact page shows: DIRECT, POTENTIAL, TESTS, UNRESOLVED, EXTERNAL nodes.
4. Expand a POTENTIAL node to show the explainable dependency path.

**Point out:** BFS over the real relationship graph, cycle-safe, every reached
node backed by per-step evidence, no risk scores — this is static analysis.

### 7. Generate review (≈ 5s)

1. From the Impact page, click **Create Review**.
2. The review page shows: REQUIRED / RECOMMENDED / INFORMATIONAL counts, a
   priority-grouped checklist.
3. Expand an item to show the evidence checklist with file + line entries.

**Point out:** deterministic from persisted findings, stable item keys carry
status across regenerations, no LLM involved.

### 8. Lens (optional, no provider)

1. If no `REPOLENS_AI_API_KEY` is set, any Lens button returns a clear
   "Provider not configured" state — the deterministic workflow remains fully
   functional.
2. If a key is available, click Explain on the review → Lens explains the
   review from bounded, evidence-selected context.

**Point out:** Lens is optional, never required, metadata-only audit logging.

---

## What to point out

- Every relationship is from real parsed source — never hardcoded.
- Every edge has source-level evidence (file + line).
- UNRESOLVED and EXTERNAL are explicit — precision over recall.
- Lens is advisory and evidence-bounded — the deterministic layers are the
  source of truth.
- The full workflow runs offline (except git clone and optional Lens call).
- Security hardening: trusted hosts, path containment, resource caps,
  sanitized errors.

---

## Known limitations to mention

- Only public GitHub HTTPS repositories are accepted.
- Python, JavaScript, and TypeScript are supported (more can be added).
- Symbol identity is not globally disambiguated across modules.
- UNRESOLVED volume grows with repo size (stdlib-heavy repos).
- Lens quality depends on the configured LLM provider.
- The tool is local and single-user (no auth, no collaboration).