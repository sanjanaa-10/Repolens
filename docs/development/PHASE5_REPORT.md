# Phase 5 Report — Real Git Diff Analysis

**RepoLens** · Phase 5 of 10

---

## 1. Objective

Answer **"WHAT CHANGED?"** between two revisions of a repository — deterministically,
offline, and backed by real Git history. RepoLens must show the developer exactly which
files, hunks, lines, and parsed symbols differ between a base and a head revision, so they
can understand a real change before writing or reviewing code. The explicit boundary: Phase 5
does **not** predict impact, breakage, or risk — that is Phase 6.

## 2. Scope

**In scope:**
- Safe Git subprocess execution (argument arrays, controlled environment, no hooks/drivers).
- Strict revision validation and resolution, including shallow-clone recovery via a bounded
  on-demand fetch.
- Two-pass diff extraction: file summaries (`--numstat` + `--name-status`) and per-file unified
  hunks (`--unified=3`).
- Persisted diff model: `Diff`, `DiffFile`, `DiffHunk`, `DiffChangedLine`, `DiffSymbol`.
- File status handling (`ADDED / MODIFIED / DELETED / RENAMED / COPIED / TYPE_CHANGED`),
  binary detection, and file classification (`SOURCE / TEST / CONFIG / DOCUMENTATION / UNKNOWN`).
- Changed-line → symbol mapping against the parsed symbol graph.
- API endpoints and a functional frontend **Changes** workspace with symbol → investigation deep-links.
- Deterministic fixture repo tests, limits, security tests, and real-repo verification.

**Out of scope (explicitly deferred):** impact prediction, affected-file traversal, risk scores,
review checklists, and LLM interpretation of diffs — all Phase 6 features.

## 3. Requirements Implemented

- Safe, isolated `git` subprocess wrapper (`git_service.py`) with no `shell=True`.
- Revision validation that blocks empty / over-long shells, option injection (`-` prefix),
  shell metacharacters, `..` traversal, and disallowed characters — **before** any git invocation.
- Resolution to full SHAs via `git rev-parse --verify`, with `RevisionNotFoundError` /
  `RevisionValidationError` mapped to clean API errors.
- On-demand shallow-clone deepening: `git fetch --depth N origin <revision>` when a revision is not
  present locally, with offline fallback to a clear error.
- Diff extraction: per-file status + ±counts + binary flag; per-file hunks with old/new positions
  and per-line `ADDED`/`DELETED` markers on the correct side (OLD/NEW).
- Rename/copy handling preserving old and new paths (status `R`, `C`; brace-notation translation).
- Idempotent persistence: unique `(repository_id, base_revision, head_revision)`; repeating a diff
  returns the stored row, never duplicates.
- Hard limits: `MAX_DIFF_FILES=500`, `MAX_DIFF_HUNKS=5000`, `MAX_DIFF_CHANGED_LINES=50000`,
  `MAX_DIFF_OUTPUT_BYTES=16MB`, `MAX_GIT_TIMEOUT_SECONDS=60`, `MAX_REVISION_LENGTH=128`.
- Symbol mapping and change-type (`ADDED`/`MODIFIED`/`DELETED`) computed from per-file line counts.
- File classification by deterministic path heuristics.
- API: create/retrieve diff, list files, list symbols, per-file hunks; 4xx/5xx error mapping.
- Frontend: revision inputs with presets, summary strip, changed-files list, per-file hunks,
  affected symbols, and deep-link navigation into the existing investigation workspace.

## 4. Architecture

```
/user request
   │  POST /repositories/{id}/diff {base_revision, head_revision}
   ▼
api/routes.py ──▶ analysis/diff/engine.py (DiffEngine.analyze)
                     │
                     ├── 1. repository lookup + idempotency check (unique revision pair)
                     ├── 2. _resolve_or_deepen (validate → resolve → bounded fetch fallback)
                     ├── 3. get_numstat (--name-status authoritative + --numstat counts)
                     ├── 4. create Diff + DiffFile rows
                     ├── 5. per-file get_diff_output → _parse_hunks → DiffHunk/DiffChangedLine
                     ├── 6. _load_symbols_for_file → _map_symbols (symbol range overlap)
                     └── 7. commit; summary returned
   │
   ▼
analysis/diff/service.py ── read layer: Diff/DiffFile/DiffHunk/DiffChangedLine/DiffSymbol → schemas
```

- `git_service.py` — the only module that spawns `git` (argument arrays, controlled env,
  `CREATE_NEW_PROCESS_GROUP` on Windows, timeout).
- `limits.py` — central constants for all caps.
- `classifier.py` — pure, deterministic path-based classification.
- `engine.py` — the idempotent orchestrator (write path).
- `service.py` — schema assembly for reads (API path).
- Everything is async over SQLAlchemy; git subprocess calls are synchronous and bounded by timeout.

## 5. Data Model

New tables (all FK → existing/added parents, `ondelete=CASCADE`):

| Table | Columns | Notes |
| ----- | ------- | ----- |
| `diffs` | id, repository_id, base_revision, head_revision, merge_base, files_changed, insertions, deletions, symbols_changed, computed_at | `UNIQUE (repository_id, base_revision, head_revision)` → idempotency |
| `diff_files` | id, diff_id, path, status, old_path, new_path, additions, deletions, binary, file_category, old_file_id, new_file_id | `old_path`/`new_path` for renames/copies; file_id links to `files` |
| `diff_hunks` | id, diff_file_id, header, old_start, old_count, new_start, new_count, content | unified-diff hunk header + body |
| `diff_changed_lines` | id, diff_hunk_id, side (`OLD`/`NEW`), line_number, change_type (`ADDED`/`DELETED`) | one row per changed line |
| `diff_symbols` | id, diff_id, symbol_id, file_path, symbol_name, symbol_kind, change_type, added_lines, deleted_lines | mapped to `symbols`; `ADDED/MODIFIED/DELETED` |

`computed_at` defaults to now; the DB is plain SQLite with `Base.metadata.create_all`, so new
tables are created automatically at startup (`init_db`).

## 6. Git Revision Handling

- **Before any git call**, the revision string is validated:
  - not empty / whitespace; length ≤ 128;
  - no leading `-` (option injection);
  - no shell metacharacters `[;&|`$(){}! \n \r]`;
  - no `..` (range / traversal);
  - only `[A-Za-z0-9._/~^@*-]` (covers SHAs, tags, branch names, `HEAD~n`, refs).
- **Resolution** via `git rev-parse --verify` → full SHA; failures raise `RevisionNotFoundError`.
- **Shallow-clone recovery (`--depth 1` ingestions):** if a revision is not present locally, the
  engine runs `git fetch --depth N origin <revision>` (bounded to tip+parent by default; N is at
  most 2) with the controlled env and timeout. If the fetch fails (offline/not reachable) a clear,
  safe error is returned. The working tree is never checked out.
- **Empty tree:** the canonical empty-tree SHA `4b825dc6…` resolves with zero fetches and is the
  default "snapshot" base in the UI.
- Determined SHAs are stored on the `Diff` row and used for all subsequent `git diff` calls
  (tree-to-tree, no worktree dependency).

## 7. Diff Engine

Two-pass, per-repository, bounded:

1. `git diff --diff-filter=ACDMRT --find-renames --find-copies --name-status` → authoritative
   paths + status codes (including `R`/`C` old → new).
2. `git diff --diff-filter=ACDMRT --find-renames --find-copies --numstat` → ±line counts; binary
   files appear as `-`, and rename brace notation (`auth/{util.py => utils.py}`) is translated to
   the concrete new path.
3. Per changed non-binary file: `git diff --no-color --unified=3 <base> <head> -- <path>` →
   unified hunks parsed into old/new starts, counts, header, content, and per-line
   `ADDED`/`DELETED` entries with the correct OLD/NEW line numbers.
4. Caps are enforced incrementally: file count, hunk count, changed-line count, and raw output
   bytes all abort with a clear `413`-style error instead of exhausting memory.

All validation runs **before** any git invocation (`ensure_revision_resolvable` calls the
validator first), preventing option/arg injection into `git`.

## 8. Symbol Mapping

- For each changed file, the symbol ranges for that file are loaded from the indexed `symbols`
  table (the parsed HEAD state) — **never** a repository rescan.
- Every changed NEW-side line is tested for overlap with `symbol.line_start..line_end`; a symbol
  is affected if any added or deleted line falls inside its block.
- A line outside every symbol maps to nothing (`NULL` symbol) rather than forcing the nearest one.
- Change type per symbol is derived from **per-file** line counts (not the whole diff):
  `ADDED` when only increments in the file, `DELETED` when only decrements, otherwise `MODIFIED`.
- Verified end-to-end: flask's `app.query` route-decorator commit mapped `Scaffold`/`post`/`query`
  as `ADDED` and `Flask` as `MODIFIED`; itsdangerous' deprecation-removal commit mapped the four
  removed imports (`base64_decode`, `want_bytes`, `BadSignature`, `BadHeader`).

## 9. API

| Method | Endpoint | Purpose | Errors |
| ------ | -------- | ------- | ------ |
| POST | `/api/repositories/{id}/diff` | `{base_revision, head_revision}` → `201` + `DiffInfo`; idempotent | 404 repo, 400 validation, 422 unresolved, 413 limits |
| GET | `/api/repositories/{id}/diff/{diffId}` | `DiffDetail` (summary + files + symbols) | 404 |
| GET | `/api/repositories/{id}/diff/{diffId}/files` | `list[DiffFileInfo]` | 404 |
| GET | `/api/repositories/{id}/diff/{diffId}/files/{diffFileId}` | `DiffHunkList` (file + hunks + changed lines) | 404 |
| GET | `/api/repositories/{id}/diff/{diffId}/symbols` | `list[DiffSymbolInfo]` | 404 |

Responses expose only computed, user-safe data; git failures become generic controlled messages
with details logged server-side.

## 10. Frontend

- Route `changes` (sidebar already shipped the entry) now renders `ChangesPage` → `ChangeView`:
  - **Revision bar** with base/head inputs and presets: *Snapshot (empty → HEAD)* and
    *HEAD~1 → HEAD*. A diff auto-runs on load (snapshot), so single-commit repos are usable
    immediately.
  - **Summary strip**: short SHAs, computed-at, files, insertions, deletions, symbols changed.
  - **Changed-files list** (left): status badge, ±counts, category, symbol count; renames show
    the struck-through old path.
  - **File detail** (right): affected symbols (badge-colored ADDED/MODIFIED/DELETED, ±counts) and
    per-hunk views with OLD/NEW line numbers and add/delete markers. Binary files state no textual
    diff.
  - **Navigation**: clicking a changed symbol deep-links to
    `/repo/{id}/investigate?symbol={id}`; `InvestigatePage` now reads the `symbol` query param and
    `SearchWorkspace` auto-loads that symbol's investigation + source context.
- Types mirror the backend schemas (`DiffInfo`, `DiffDetail`, `DiffFileInfo`, `DiffSymbolInfo`,
  `DiffHunkInfo`, `DiffChangedLineInfo`, `EMPTY_TREE_SHA`, …); `client.ts` adds `createDiff`,
  `getDiffDetail`, `getDiffFiles`, `getDiffSymbols`, `getDiffFileHunks`.

## 11. Testing

`tests/test_diff_engine.py` — 15 tests against a **real two-commit git fixture** (module-scoped,
built with safe subprocess git):

- Persisted summary + statuses verified against `git diff --name-status` computed in-test
  (references, not hardcoded values): MODIFIED / ADDED / DELETED / RENAMED + old_path.
- Hunk/line counts checked against `git diff --numstat` (independent oracle).
- Binary file detection (built-in `assets/logo.bin`) with zero hunks.
- Symbol mapping: surviving method gains lines → `MODIFIED`; new class in an added file →
  `ADDED`; category classification for SOURCE/TEST/CONFIG/DOCUMENTATION.
- Idempotency (same pair → same `Diff` id, no duplicate rows), including a short-SHA vs
  full-SHA retry hitting the identical row (regression for the resolve-before-check bug).
- Repository isolation (two repos, separate rows).
- Security: 11 malicious / malformed revisions rejected; unknown revision → not found.
- Same base/head → rejected.
- API: create (201) + idempotent recreate, detail/files/symbols/hunks retrieval, unknown-rev
  error mapping, missing diff/repo → 404.

**Full suite:** `176 passed, 2 skipped, 4 deselected` (was 161 passing at end of Phase 4; +15 diff).

## 12. Real Repository Metrics

Environment: Windows, Python 3.12, SQLite, shallow clones deepened on demand. Diffs:
- **itsdangerous** `4dffa19` → `672971d` (drop EOL pythons + remove deprecated code + logo):
  **11 files (+99/−44)** — DB and `git diff --shortstat` agree exactly. Classification: 1 CONFIG
  (workflow yaml), 3 DOCUMENTATION, 3 SOURCE-ish (docs/conf.py) / 3 SVG assets (UNKNOWN),
  1 SOURCE (`src/itsdangerous/__init__.py`, −21 lines, **4 affected symbols**).
  Wall time ≈ **1.4 s**.
- **flask** `514fc6b` → `d73fa1c` (app.query route decorator + IPv6 session fixes):
  **8 files (+43/−12)** — DB and git agree exactly. 5 SOURCE / 2 TEST / 1 DOCUMENTATION,
  **19 affected symbols** (`Flask` MODIFIED, `Scaffold` + `post`/`query` ADDED,
  `test_session_transaction_ipv6` ADDED, …). Wall time ≈ **1.2 s**.

The symbol mapping truthfully reflects the actual commit content — verified against the real
upstream commit subjects, no fabricated hits.

## 13. Security

- Repository contents remain untrusted input; RepoLens **never executes** repository code.
- `git` runs with argument arrays (no `shell=True`), `--no-ext-diff`, and a controlled env
  (`GIT_TERMINAL_PROMPT=0`, `GIT_EDITOR=""`, `GIT_PAGER=cat`, `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_DISCOVERY_ACROSS_FILESYSTEM=0`, `LC_ALL=C`) — no hooks, aliases, diff drivers, or pager.
  On Windows, `CREATE_NEW_PROCESS_GROUP` isolates the child.
- Revision validation precedes every git call, blocking `-` options, metacharacters, `..`,
  over-long strings, and empty input. Fetches are depth-bounded and non-interactive.
- Per-file paths come only from `git`'s own relative names; nothing user-supplied is passed to
  the shell. All work happens inside the repository's stored `local_path`.
- Errors expose safe, generic messages; technical details are logged server-side.

## 14. Limitations and Deviations

- **Scope: "what changed" only.** No impact prediction, no risk scoring, no "will break X" —
  per phase boundary. Do not infer severity from `symbol_change_types`.
- **Shallow clones.** Ingestions use `--depth 1`. To diff real commit pairs the engine deepens via
  a bounded fetch; offline or unreachable revisions produce a clear error, and only the empty-tree
  snapshot remains available. This is the documented design choice: simplest correct fix without a
  second ingestion system. Verified working on both pallets repos.
- **Symbol mapping is HEAD-based.** The parsed index reflects the head revision. Deleted symbols
  (whole removed files) and deleted-only lines cannot attach to a surviving HEAD symbol, so
  `DELETED` mappings appear only when a surviving symbol's block loses lines. The UI still
  surfaces the file-level `DELETED` status for those files.
- **Merge commits.** A tree-to-tree diff (`git diff A B`) is used, so combined (merge) changes are
  shown as the net difference between the two trees — deterministic and documented.
- **Rename/copy heuristics** depend on git's `--find-renames/--find-copies` defaults; exotic
  mixed-content renames may appear as ADD/DELETE pairs.
- **Very large diffs** beyond the caps (500 files / 5000 hunks / 50k lines / 16 MB) are rejected
  with a clear 413-style message instead of being processed partially.
- **The prod DB** (`backend/data/repolens.db`) was upgraded in place via `init_db`
  (`create_all`) — the new tables were added; the existing schema is untouched.

## 15. Files Changed, Run Instructions and Next Steps

**Backend (new):**
- `app/analysis/diff/__init__.py`, `app/analysis/diff/limits.py`
- `app/analysis/diff/classifier.py` — path-based file classification
- `app/analysis/diff/git_service.py` — safe git exec, revision validation, deepen, numstat/name-status, diff output
- `app/analysis/diff/engine.py` — idempotent orchestrator + hunk parser + symbol mapping
- `app/analysis/diff/service.py` — read layer → API schemas

**Backend (changed):**
- `app/models/orm.py` — added `Diff`, `DiffFile`, `DiffHunk`, `DiffChangedLine`, `DiffSymbol`
- `app/models/schemas.py` — added diff schemas (`FileStatus`, `FileCategory`, `DiffInfo`,
  `DiffDetail`, `DiffFileInfo`, `DiffSymbolInfo`, `DiffHunkInfo`, `DiffChangedLineInfo`, `DiffHunkList`)
- `app/api/routes.py` — 5 new diff endpoints (+ `GitError`/`DiffChangedLineInfo` imports)

**Frontend (new):**
- `src/pages/ChangesPage.tsx`
- `src/components/analysis/ChangeView.tsx`

**Frontend (changed):**
- `src/App.tsx` — `changes` route → `ChangesPage`
- `src/api/client.ts` — diff API functions
- `src/types/index.ts` — diff types + `EMPTY_TREE_SHA`
- `src/pages/InvestigatePage.tsx`, `src/components/analysis/SearchWorkspace.tsx` —
  `?symbol=` deep-link support

**Tests:** `tests/test_diff_engine.py` (14 tests)

**Run instructions:**
```bash
cd backend
py -3.12 -m venv .venv                      # if not created
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest            # 176 passed, 2 skipped, 4 deselected
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
cd ../frontend && npm install && npm run dev   # http://localhost:5173
# Open a repository's "Changes" page; pick revisions or use presets.
```

**Next steps — Phase 6 (Change review + impact simulation):** consume `Diff` +
`DiffSymbol` to traverse the relationship graph from changed symbols to callers/refs/tests,
compute proven vs. potential impact, generate a review checklist, and — only then — present an
"impact" view. Phase 5 explicitly does not do that today.