# Phase 7 Report — Deterministic Change Review Workflow

**RepoLens** · Phase 7 of 10

---

## 1. Objective

Deliver `DIFF → IMPACT → REVIEW → ACTIONABLE CHECKLIST → EVIDENCE` as a
deterministic, offline workflow. From a persisted Phase 6 impact analysis —
**never recomputed** — RepoLens assembles a per-repository change review: a
priority-ordered checklist of items to inspect before merging, where every
item and every checklist entry is generated purely from verified diff and
impact findings and carries the exact file + line evidence plus the impact
path that explains why it is affected.

The boundary is identical to Phases 5 and 6: the review is evidence-based
**static** analysis. It is not a correctness guarantee, a risk score, an LLM
judgment, or a merge gate.

> Review items are generated deterministically from verified diff and impact
> findings. No LLM is used to decide checklist content or priority.

## 2. Scope

**In scope:**
- Three new tables: `Review`, `ReviewItem`, `ReviewItemEntry`, all
  repository-scoped and idempotently keyed.
- Four new API endpoints: create/reuse a review from an impact analysis, read
  a review, update an item (status + notes), toggle a checklist entry.
- A **pure** checklist generator (`ImpactAnalysisInfo → ReviewSpec`) that is
  byte-deterministic: identical inputs → identical outputs, enforced by
  stable item keys and entry keys.
- Deterministic priority (`REQUIRED` / `RECOMMENDED` / `INFORMATIONAL`) and
  item types backed only by categories that actually exist in Phase 6 output.
- Evidence entry checklist per item: exact file + line for each entry,
  per-entry impact path with step-by-step evidence, and deep-links to source,
  investigation, and the impact graph.
- Frontend **Review** page: scorecard (REQUIRED / RECOMMENDED / INFORMATIONAL
  / Completed counts only — no risk numbers), priority-grouped foldable
  items, filters, per-item status + notes editing, test sub-checklist with
  per-entry toggles, and "Create review" from the Impact page.
- Deterministic fixture tests (priority/grouping rules, caps, sanitization,
  API lifecycle, idempotency, carry-over, validation, isolation), full-suite
  regression, and real-repo verification on pallets/flask.

**Out of scope (explicitly deferred to Phase 8):** LLM explanation of items,
risk scores or severity models, merge blocking / CI gates, cloud or
collaborative features. None of these are simulated or implied by the
deterministic review.

## 3. Requirements Implemented

- `POST /api/repositories/{repository_id}/impact/{analysis_id}/review` with
  body `{}` or `{"regenerate": true}`. Creates a review (`201`) or reuses the
  latest stored review for the same repository + diff + analysis (`200`).
  `regenerate: true` inserts a fresh row carrying status and notes forward by
  stable key.
- `GET /api/repositories/{repository_id}/reviews/{review_id}` returns the
  full `ReviewInfo`: summary, revisions, priority counts, items, entries,
  evidence, and paths.
- `PATCH /api/repositories/{repository_id}/reviews/{review_id}/items/{item_id}`
  updates `{status?: OPEN|IN_PROGRESS|DONE|SKIPPED, notes?: str ≤ 4000}` →
  `ReviewItemInfo`.
- `PATCH /api/repositories/{repository_id}/reviews/{review_id}/items/{item_id}/entries/{entry_id}`
  toggles `{status: OPEN|DONE}` → `ReviewEntryInfo`.
- Eight item types, restricted to categories that Phase 6 findings actually
  produce: `CHANGED_CODE`, `AFFECTED_CALLER`, `AFFECTED_DEPENDENCY`,
  `AFFECTED_TEST`, `UNRESOLVED_IMPACT`, `EXTERNAL_DEPENDENCY`,
  `CONFIGURATION_CHANGE`, `DOCUMENTATION_CHANGE`.
- Deterministic `REQUIRED / RECOMMENDED / INFORMATIONAL` priority (see §7),
  and `ReviewItemStatus` + `ReviewEntryStatus` workflows.
- Stable `(review_id, stable_key)` uniqueness for items and
  `(review_item_id, entry_key)` for entries → idempotent persistence and
  carry-over across regenerations.
- `MAX_REVIEW_ENTRIES_PER_ITEM = 200` cap with counts preserved and a
  truncation note; `MAX_REVIEW_NOTES_LENGTH = 4000`.
- Security/sanitization: control characters stripped, secret-looking values
  (`API_KEY/PASSWORD/TOKEN/SECRET/PRIVATE_KEY`) redacted, config items store
  file/line/count only (never config content), hostile paths stored as inert
  text. All queries repository-scoped → cross-repo access is `404`.

## 4. Architecture

```
POST /repositories/{id}/impact/{analysisId}/review {regenerate?}
   │
   ▼
review.service.create_review(db, repo, analysis_id, regenerate)
   │  load persisted ImpactAnalysis + Diff (by analysis_id)  (Phase 6 rows)
   │  reuse latest Review for (repo, diff, analysis)  unless regenerate
   │
   ▼
review.generator.generate_review_spec(info)          ── PURE ----------·
   │  consumes ImpactAnalysisInfo (analysis_to_info, never recomputes)   │
   │  changed files/symbols → CHANGED_CODE / CONFIG / DOC items          │
   │  __init__-scoped source files → CONFIGURATION_CHANGE item           │
   │  paths of POTENTIAL test nodes → AFFECTED_TEST item (test rows)     │
   │  caller paths (CALLS/REFERENCES/EXTENDS/IMPLEMENTS) → AFFECTED_     │
   │    CALLER buckets split by direct (depth 1) vs transitive (rank >1) │
   │  IMPORTS paths → AFFECTED_DEPENDENCY buckets                        │
   │  UNRESOLVED reachable nodes → UNRESOLVED_IMPACT item                │
   │  EXTERNAL imports → EXTERNAL_DEPENDENCY item                        │
   │  per-entry shortest-path + tests sub-checklist + doc item           │
   └──────────────────  returns ReviewSpec (ItemSpec[]/EntrySpec[])  ----·
   │
   ▼
review.service.persist + render: stable-key upsert, carry-over state,
   deterministic ordering by (priority, type, index)  →  ReviewInfo
```

- `generator.py` is a pure module: given an `ImpactAnalysisInfo` it returns a
  `ReviewSpec`; no DB, no randomness, no I/O. Determinism is test-enforced.
- `policy.py` owns types, priorities, statuses, and limits.
- `service.py` owns persistence, `GET`/`PATCH` reads, carry-over, sanitization
  on render, and clean 404s.
- All entry kind labels map 1:1 to the Phase 6 classifications they came from
  (`FILE`, `CALLER`, `TEST`, `UNRESOLVED`, `EXTERNAL`, `DOC`).

## 5. Review Model

Three new tables appended in `backend/app/models/orm.py` (no existing table
or column changed):

- **`Review`** — `id`, `repository_id`, `diff_id`, `impact_analysis_id`,
  `title`, `summary`, `status`, `base_revision`, `head_revision`,
  `max_depth`, `created_at`, `updated_at`. No uniqueness constraint on the
  triplet: reuse is found by latest `id`; regeneration inserts a new row.
- **`ReviewItem`** — `id`, `review_id`, `stable_key` (unique per review),
  `item_type`, `title`, `description`, `status`, `priority`, `source_type`,
  `source_id`, `evidence_file_id`, `evidence_start_line`,
  `evidence_end_line`, `group_via`, `notes`, position index.
- **`ReviewItemEntry`** — `id`, `review_item_id`, `entry_key` (unique per
  item), `entry_type`, `title`, `description`, `status` (`OPEN`/`DONE`),
  `symbol_id`, `file_id`, `line_number`, `order_index`, plus a JSON column
  holding the impact-path steps captured at generation time.

Reviewed API shapes: `ReviewInfo` wraps `ReviewSummaryInfo`, `ReviewItemInfo`,
`ReviewEntryInfo`. `ReviewSummaryInfo` exposes only counts — there is no risk
or score number anywhere in the model.

## 6. Checklist Generation

- **CHANGED_CODE** — one item per changed file, with per-symbol sub-entries
  (`CHANGED_CODE:{file}:{symbol}`) plus a FILE_LEVEL entry when the diff could
  not be mapped onto any symbol. Entries carry the symbol/file evidence and an
  `[Open Source]` deep-link.
- **CONFIGURATION_CHANGE** — files in `__init__`/config scope changed by the
  diff; entries store file + line + count only.
- **DOCUMENTATION_CHANGE** — changed doc files; single informational item.
- **AFFECTED_TEST** — test nodes reached by the impact BFS: one test item with
  one togglable checklist entry per test file/function, each entry backed by
  its impact path. Checking an entry confirms the test was reviewed.
- **AFFECTED_CALLER** — non-test callers reached via CALLS/REFERENCES/
  EXTENDS/IMPLEMENTS, bucketed per relationship type; entries are the caller
  symbols with evidence + paths.
- **AFFECTED_DEPENDENCY** — non-test files reached via IMPORTS; entries are
  the dependents.
- **UNRESOLVED_IMPACT** — reachable `UNRESOLVED` nodes of changed entities;
  every entry is a countable flag with evidence, capped at
  `MAX_REVIEW_ENTRIES_PER_ITEM` (200) with the remainder counted in the item
  description.
- **EXTERNAL_DEPENDENCY** — imports from changed files pointing outside the
  repository; entries are evidence flags.
- Every entry stores its impact path (`path_steps`) frozen at generation time,
  and the review summary explicitly explains the pipeline
  (diff → impact → checklist → evidence).

## 7. Priority and Aggregation Policy

`policy.py` decides priority from **observable category + reachability**,
never from a model:

| Priority | Items |
| -------- | ----- |
| `REQUIRED` | `CHANGED_CODE`, `CONFIGURATION_CHANGE`, `AFFECTED_TEST`, and **direct** caller buckets (callers at depth 1 reached via one `CALLS`/`REFERENCES`/`EXTENDS`/`IMPLEMENTS` edge). |
| `RECOMMENDED` | transitive caller buckets (depth > 1), `AFFECTED_DEPENDENCY` (IMPORTS), `UNRESOLVED_IMPACT`, `EXTERNAL_DEPENDENCY`. |
| `INFORMATIONAL` | `DOCUMENTATION_CHANGE`. |

Caller aggregation splits direct vs transitive per relationship type with
stable keys `AFFECTED_CALLER:{via}:direct` / `AFFECTED_CALLER:{via}:transitive`
so re-generation merges rather than duplicates. Items are ordered by
`PRIORITY_ORDER*1000 + ITEM_TYPE_ORDER*100 + index` — REQUIRED first, then the
fixed category order within each priority — and item lists are always
byte-identical for identical analysis. Welcome, honest note: priority is a
review-workflow ordering, not a risk measurement.

## 8. Evidence and Navigation

- Every item shows its primary evidence (`evidence_file:evidence_start_line`).
- Every entry carries `file_path`, `line_number`, `symbol_id`, and a frozen
  `path_steps` chain (`source → relationship → target` + proof line).
- Frontend deep-links per entry: **[Open Source]** →
  `/investigate?file=<path>` (opens the file in the source view),
  **[Investigate]** → `/investigate?symbol=<id>` (loads the symbol's depth-1
  neighborhood), **[View Impact]** → `/impact?analysis=<id>` (reopens the
  Phase 6 graph), and **[Why this review?]** explains the pipeline.
- The scorecard shows only `REQUIRED / RECOMMENDED / INFORMATIONAL /
  Completed` counts; filters cover All / Open / Done / Required / Recommended
  / Tests / Unresolved / External.

## 9. API

| Method | Endpoint | Body / Params | Returns |
| ------ | -------- | ------------- | ------- |
| POST | `/api/repositories/{rid}/impact/{aid}/review` | `{regenerate?: bool}` | `ReviewInfo` (`201` created, `200` reused) |
| GET  | `/api/repositories/{rid}/reviews/{vid}`     | — | `ReviewInfo` |
| PATCH| `/api/repositories/{rid}/reviews/{vid}/items/{iid}` | `{status?, notes?}` | `ReviewItemInfo` |
| PATCH| `/api/repositories/{rid}/reviews/{vid}/items/{iid}/entries/{eid}` | `{status: OPEN\|DONE}` | `ReviewEntryInfo` |

Errors: malformed statuses / notes > 4000 → `422`; missing repository,
analysis, review, item, or cross-repo access → `404`; validation errors carry
user-safe messages. `POST` with `regenerate: true` returns `201` and persists
a new row after deterministically carrying open status/notes forward by stable
key.

## 10. Frontend

New files: `frontend/src/pages/ReviewPage.tsx` and
`frontend/src/components/analysis/ReviewView.tsx`. The `/repo/:id/review`
route (previously a `WorkInProgress` placeholder) is wired in `App.tsx`, with
the sidebar nav entry already present in `AppShell.tsx`.

- `ReviewPage` reads `?review=`, `?analysis=`, `?diff=`; `ReviewView` loads
  the review and, when only an analysis is provided, offers the deterministic
  **Create review** action. Created/regenerated reviews update the URL via
  `replace`.
- `ReviewView`: header with `base → head`, depth, status, and **Regenerate**
  (same analysis, fresh row, carry-over); scorecard tiles (counts only);
  eight filters; priority-grouped foldable item cards (type + priority badges,
  evidence line, per-item status selector, notes auto-save on blur); test
  items render a per-entry checklist with completion toggles; every entry
  offers deep-links and an expandable impact path.
- `ImpactView` gained **Create review** (creates the review and navigates with
  `review`/`analysis`/`diff` params) and an `initialAnalysisId` deep-link that
  reloads a stored analysis from `?analysis=`.
- `SearchWorkspace` gained `initialFile` so **Open source** lands directly in
  the source view; `InvestigatePage` reads `?file=`.
- A deterministic-honesty note is shown on the review page and the README
  states the no-LLM guarantee verbatim.

## 11. Testing

`backend/tests/test_review_engine.py` — **15 tests**, all passing, reusing the
Phase 6 fixture builders (`from tests.test_impact_engine import ...`):

- Priority + type mapping for every category, incl. the direct vs transitive
  caller split and IMPORTS → `AFFECTED_DEPENDENCY`.
- Byte determinism: two identical POSTs return identical lists; two model
  generations produce identical specs.
- Entry cap: a 250-entry item yields exactly 200 stored/listed entries with
  "250" preserved in the title and a cap note in the description.
- Per-entry path steps attached; shortest-path selection.
- Redaction of secret-looking values, control-char sanitization, and hostile
  paths stored as inert text.
- API lifecycle: categories + counters + ordering + byte-identical
  GET == POST; idempotent reuse returns the same id via `200`.
- Status + notes persist; entry toggle works; `422` on bad status / notes >
  4000 / bad entry status.
- Regenerate creates a new review while carrying status + notes forward.
- Repository isolation: a second repo cannot read, write, or touch another
  repo's reviews (`404` everywhere); unknown review/item → `404`.

Full backend suite after Phase 7: **202 passed, 2 skipped, 4 deselected**
(187 passing before; no Phase 1–6 regression). Frontend: `npm run build`
(type-check + vite) is green.

## 12. Real Repository Metrics

Real review generated against the productive SQLite DB
(`backend\data\repolens.db`): repository pallets/flask (repo 2), diff
`514fc6b → d73fa1c` (diff 2), persisted Phase 6 analysis (analysis 1).

| Metric | Value |
| ------ | ----- |
| Items | **31** |
| Required / Recommended / Informational | **24 / 6 / 1** |
| Generation (pure spec build) | **~70.6 ms** |
| Persist + render | **~693.3 ms** |
| Entries by kind | FILE 33, CALLER 84, TEST 5, UNRESOLVED 200 (capped of 215), EXTERNAL 7, DOC 1 |
| Items by type | CHANGED_CODE 20, AFFECTED_CALLER 3+3 (direct/transitive), AFFECTED_DEPENDENCY 1, AFFECTED_TEST 1, UNRESOLVED_IMPACT 1, EXTERNAL_DEPENDENCY 1, DOCUMENTATION_CHANGE 1 |

Two review rows exist (ids 1, 2): the natural `201` create and a
`{"regenerate": true}` run that produced an identical checklist while keeping
status/notes — the deterministic guarantee reproduced on real data.

## 13. Security and Performance

- Repository contents remain untrusted input. Review text is run through
  `clean_text` (control characters stripped) and `redact_secret`
  (regex-replaces `API_KEY/PASSWORD/TOKEN/SECRET/PRIVATE_KEY` values with
  `key=REDACTED`); config items never embed config content.
- Hostile paths (`../../etc/passwd`) are stored as inert text and never
  resolved server-side; all paths are repository-relative.
- Every query is scoped to the requesting repository; existence checks run
  before reads/writes so cross-repo IDs surface as `404`, not leaks.
- Performance is bounded: pure generation is tens of milliseconds; the
  dominant cost is the single SQLite render pass. Entry lists are capped at
  200 per item so output can never balloon; hard iteration caps already
  bound the underlying diff and impact layers.

## 14. Limitations and Deviations

- **A checklist is not a correctness verdict.** Items list what to inspect;
  confirming them does not guarantee runtime impact or correctness.
- **Priority is workflow priority, not risk** and is derived only from
  category + directness (see §7).
- **Items inherit the precision of static analysis.** Callers/dependencies/
  tests come only from resolved relationships; dynamic couplings are absent
  (`UNRESOLVED`/`EXTERNAL` are counted flags with evidence, never expanded).
- **A changed method's enclosing class is also flagged** (intentional
  cross-check), so a body edit can show a class-level caller item too.
- **Deviations from the original plan:** the `Review` table was not given a
  unique `(repository_id, diff_id, impact_analysis_id)` constraint — reuse
  finds the latest row by `id` and regeneration inserts a new one, which is
  simpler and still idempotent. `evidence_file`/`line` are stored directly on
  items/entries (no separate evidence table). Caller aggregation split
  direct vs transitive per `via` (REQUIRED vs RECOMMENDED). `ImpactClass` has
  no `TESTS` value — test nodes are `POTENTIAL` with `is_test=true`, and the
  review keys its TESTS checklist off `is_test`, aligned with Phase 6 output.
- **Bugs found during the phase:** `ImpactPathInfo` has no `id` field, so the
  `_PathIndex` sorts by `(depth, target)`; `datetime.utcnow` deprecation in
  `service.py` → `datetime.now(timezone.utc).replace(tzinfo=None)`.
- No risk model, no merge gate, no LLM — by design; see Phase 8 next steps.

## 15. Files Changed, Run Instructions and Next Steps

**Backend**
- `backend/app/models/orm.py` — `Review`, `ReviewItem`, `ReviewItemEntry`
  tables appended.
- `backend/app/models/schemas.py` — Phase 7 schemas appended
  (`ReviewCreateInput`, update inputs, `Review*Info`, `ReviewSummaryInfo`).
- `backend/app/analysis/review/__init__.py` — package exports.
- `backend/app/analysis/review/policy.py` — types, priorities, statuses, limits.
- `backend/app/analysis/review/generator.py` — pure `ReviewSpec` generation
  (items, entries, paths, caps, redaction/sanitization helpers).
- `backend/app/analysis/review/service.py` — persistence, reads, carry-over,
  rendering, repo-scoped 404s.
- `backend/app/api/routes.py` — four Phase 7 endpoints.
- `backend/tests/test_review_engine.py` — 15 tests.

**Frontend**
- `frontend/src/types/index.ts` — Phase 7 types.
- `frontend/src/api/client.ts` — `createReview`, `getReview`,
  `updateReviewItem`, `updateReviewEntry`.
- `frontend/src/pages/ReviewPage.tsx`, `frontend/src/components/analysis/ReviewView.tsx`
  — Review page.
- `frontend/src/App.tsx` — review route wired.
- `frontend/src/components/analysis/ImpactView.tsx` — Create review +
  `?analysis=` deep-link; `frontend/src/pages/ImpactPage.tsx`.
- `frontend/src/components/analysis/SearchWorkspace.tsx` + `frontend/src/pages/InvestigatePage.tsx`
  — `initialFile` / `?file=` deep-link.

**Docs** — `README.md` (Status, Phase 7 feature bullet, 4 API rows, Phase 7
limitations, Phase 7 metrics, Phase 7 roadmap check), this report.

**Run instructions**

```bash
cd backend && .\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # then /repo/:id → Impact → Simulate → Create review
cd backend && .\.venv\Scripts\python -m pytest   # full suite: 202 passed
cd frontend && npm run build                    # type-check + vite build
```

**Next steps (Phase 8 — premium UI/UX refinement):** given the deterministic
review is stable, the next phase focuses on frontend polish (visual hierarchy,
keyboard workflow, previews) and, once the deterministic layers are proven,
may introduce an LLM explanation layer **on top of** the evidence —
explanations will remain references to the deterministic findings, never the
source of checklist content or priority.