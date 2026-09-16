# Phase 9 Report — Security Hardening + Production Reliability

## 1. Objective

Phase 9 makes every deterministic surface of RepoLens (INDEX, INVESTIGATE,
DIFF, IMPACT, REVIEW) and the optional Lens layer trustworthy against
malicious, oversized, and malformed input — with **no new product
capabilities**. The work is code-only hardening plus verification: central
safe Git execution, a single path-containment enforcement point, database
integrity with full cascade deletion, resource caps with loud truncation
semantics, API/HTTP hardening (request IDs, security headers, trusted hosts,
categorized errors), shared secret redaction, local concurrency guards,
schema-level input bounds, and recorded real-world dependency/repository
verification. Definition of done is the 41-item checklist below (all
completed) plus the 20-item response at the end.

### 41-item checklist

**M-01 Centralized safe Git execution**

1. Shared `app/core/git_safety.py` module exists; `acquisition.py` and `diff/git_service.py` both route through it.
2. Mandatory `-c` overrides on every invocation: `core.hooksPath=`, `credential.helper=`, `protocol.file.allow=never`, `protocol.ext.allow=never`.
3. Ambient `GIT_*`/SSH variables stripped from the subprocess environment.
4. Hardened env applied (`GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `LC_ALL=C`, pager disabled).
5. Argument-array-only enforcement (no `shell=True`) on every git path; command-first guard rejects option-leading argv.
6. Captured stdout/stderr capped at 64 MiB raising `GitSafetyError`.
7. Timeout + process-group creation (Windows) preserved on clone and diff calls.
8. Malicious-fixture repo with a `/.git/hooks` payload proves hooks never execute (end-to-end hardening test).
9. Revision validation still rejects option injection, shell metacharacters, traversal, double-dots (regression suites).
10. `CalledProcessError` / `FileNotFoundError` / `Timeout` / `GitSafetyError` map to typed errors (`GitError`, `GitUnavailable`, `CloneTimeoutError`) with raw stderr never returned to clients.

**M-02 Path containment**

11. `text_search._read_source_file` uses the canonical `safe_join`; `PathEscapeError` handled by caller.
12. No ad-hoc containment checks remain for repository reads.
13. Adversarial paths rejected: absolute, drive letter, UNC, traversal, null byte, sibling-prefix (`tests/test_security.py` + hardening path-escape test).
14. Windows read-only workspace cleanup regression retained (`tests/test_security.py`).

**M-03 Database integrity + cascade delete**

15. `PRAGMA foreign_keys=ON` applied per session (`get_db` + test override) — enforcement documented.
16. `remove_repository` deletes bottom-up through every child table (diffs→diff_files→diff_hunks→diff_changed_lines→diff_symbols, impact_analyses→nodes/paths, reviews→items→entries, lens_audits, relationships, files→symbols/imports/exports/parse_results).
17. End-to-end test: deleting a repository leaves zero child rows; a second repository's rows are untouched.
18. All queries remain parameterized; injection-string fixtures return empty/400 without errors.

**M-04 Resource caps with truncation semantics**

19. Impact unresolved-group cap (500) sets `truncated=true` + `truncate_reason`.
20. Impact external-group cap (500) sets the same flags.
21. Review item cap (300) truncates deterministically after sorting with a summary note.
22. Lens evidence cap (100) with `evidence_truncated` guard in `render()`.
23. Lens provider response capped at 1 MiB (content-length pre-check + post-read check) — fake-provider tests.
24. Caps never silently drop: every hit returns a controlled error or `truncate_reason`.

**M-05 API/HTTP hardening**

25. Request-ID middleware generates/echoes `X-Request-ID` and threads it into logs.
26. Global exception handlers return categorized bodies (`INVALID_INPUT` 422, `INTERNAL_ERROR` 500) with no stack traces/paths/env/subprocess details.
27. TrustedHostMiddleware allowlist from `settings.trusted_hosts`; `test` host rejection verified.
28. Security headers set (nosniff, frame, referrer, x-xss, cache-control, permissions-policy) — asserted by test.
29. Validation-error bodies keep a `detail` superset for client compatibility.
30. Structured-safe error text reviewed: no secrets, keys, DB paths, or subprocess output.

**M-06 Secret redaction**

31. Shared `app/core/redaction.py` with `is_secret_key` / `redact_secret` / `redact_text` / `redact_log_line`.
32. Lens context `render()` applies redaction to label, file, detail, and snippet.
33. Assignment patterns cover `=`, `:`, `:=`; compound keys like `SECRET_KEY` match as a unit before `secret`.
34. `AUTHORIZATION` / `COOKIE` header keys included.
35. Adversarial workflow test: committed `password=`/`API_KEY`/`SECRET_KEY` values never appear in the rendered lens payload; labels preserved as `REDACTED`.

**M-07 Local concurrency guards**

36. Bounded LRU lock registry (≤256 keys) — no unbounded growth.
37. Ingestion locked per canonical URL; duplicate parallel POST serialized (test).
38. Diff / impact / review creation lock-protected (tests).
39. Races return the winning row; no `IntegrityError` reaches the client (serialization test).

**M-08 Schema-level input bounds**

40. `DiffInput` revisions `min_length=1, max_length=100`; `get_file_content path` `min_length=1, max_length=2000` → `422 INVALID_INPUT` (validation tests).

**M-09 / M-10 documentation + verification**

41. Dependency audits and real-repo verification recorded with real tools/versions/results (pip-audit 2.10.1 clean after `python-multipart 0.0.9→0.0.31`; `npm audit` 7 findings documented with exploitability rationale; live clones of pallets/flask and pallets/itsdangerous verified; no `.github` CI or Docker exists by design — documented as N/A). Intentional behaviors (LIKE wildcards, 413-on-limit) documented in SECURITY.md.

## 2. Scope

In scope: hardening, robustness, and failure handling across acquisition/diff
(Git), search/impact/review limits, database integrity, HTTP/API behavior, the
Lens provider boundary, redaction, and local concurrency — plus verification
via tests, live repository clones, and dependency audits.

Out of scope (documented, not implemented): authentication/authorization,
multi-user support, OAuth, private repositories, code execution, distributed
workers/message queues, Redis, Kubernetes, Docker, SIEM/WAF/IDS, vulnerability
scanning modes, and autonomous agents. No CI exists in this repository
(`.github/` absent) and none is added; no Docker configuration exists and none
is added; these are documented N/A, not gaps introduced by Phase 9.

## 3. Mitigations Implemented

All 11 planned mitigations from the Phase 9 hardening plan are closed:

| Mitigation | Outcome |
| --- | --- |
| M-01 Git hardening | `app/core/git_safety.py`; acquisition + diff now share one runner |
| M-02 Path containment | `text_search` uses `safe_join`; one enforcement point |
| M-03 DB integrity | FK `PRAGMA` per session; full bottom-up cascade delete |
| M-04 Resource caps | Unresolved/external impact, review items, Lens evidence, Lens response bytes |
| M-05 API/HTTP | Request IDs, security headers, trusted hosts, categorized errors |
| M-06 Redaction | Shared module; applied to Lens render + review + logs/errors |
| M-07 Concurrency | Bounded in-process locks for ingest/diff/impact/review |
| M-08 Schema bounds | Revision + path length bounds → 422 |
| M-09 Intentional behaviors | Documented in SECURITY.md + this report |
| M-10 Dependency/CI verification | pip-audit clean; npm audit documented; no CI/Docker (N/A) |
| M-11 Frontend posture | Verified clean by `tsc --noEmit` + grep audit (no test runner in project; documented) |

## 4. Architecture

New shared modules (single enforcement points, per the decision model):

- `app/core/git_safety.py` — hardened env builder, `git_command`, `run_git_capture`.
- `app/core/redaction.py` — `is_secret_key`, `redact_secret`, `redact_text`, `redact_log_line`.
- `app/core/locks.py` — `protected` async lock context manager over a bounded LRU registry.
- `app/core/security.py` — `RequestIDMiddleware`, `SecurityHeadersMiddleware`, `install_error_handlers`.
- `app/core/database.py` — `enable_foreign_keys(session)` applied per connection.

Consumers: `repositories/acquisition.py`, `analysis/diff/git_service.py`,
`analysis/search/text_search.py`, `analysis/impact/engine.py`,
`analysis/review/{policy,generator,service}.py`, `ai/{client,context}.py`,
`api/routes.py`, `main.py`, `config.py`, `services/ingestion_service.py`.

## 5. Hardened Git Execution (M-01)

Every git subprocess runs as an argv array: `git -c core.hooksPath= -c
credential.helper= -c protocol.file.allow=never -c protocol.ext.allow=never …
<stdin args>`, with a `safe_env()` that strips ambient `GIT_*/GIT config` and
SSH variables, disables terminal prompting and system/global config, forces
`LC_ALL=C`, captures at most 64 MiB (else `GitSafetyError`), honors the
existing timeouts, and creates a process group on Windows for reliable
timeout kills. `acquisition._run_git` maps failures to the established typed
errors via a synthetic `CompletedProcess` so prior clone-failure classification
is unchanged.

## 6. Path Containment and Database Integrity (M-02, M-03)

`text_search._read_source_file` now resolves paths through the canonical
`safe_join`; `PathEscapeError` is caught at the caller. SQLite foreign keys
are enabled per-session (`PRAGMA foreign_keys=ON`); `remove_repository` runs an
explicit bottom-up cascade (diff hunks reference `diff_hunks`, changed lines
reference hunks — not the diff directly — so the delete walks
diffs→diff_files→diff_hunks→diff_changed_lines→diff_symbols, then
impact_analyses→impact_nodes/paths, reviews→items→entries, and finally
lens_audits, relationships, files→symbols/imports/exports/parse_results, and
the repository row). An end-to-end test asserts zero leftover rows and that a
second repository is untouched.

## 7. Resource Caps (M-04)

- `MAX_IMPACT_UNRESOLVED_NODES = 500`, `MAX_IMPACT_EXTERNAL_NODES = 500` —
  unresolved/external groups are bounded with `truncated=true` +
  `truncate_reason`.
- `MAX_REVIEW_ITEMS = 300` — items capped deterministically post-sort with a
  summary note.
- `MAX_EVIDENCE_ITEMS = 100` — Lens evidence capped with `evidence_truncated`.
- `MAX_LENS_RESPONSE_BYTES = 1 MiB` — content-length pre-check + post-read cap
  on the provider response.

## 8. API/HTTP Hardening (M-05)

`RequestIDMiddleware` (generate/echo `X-Request-ID`, threaded into logs),
`SecurityHeadersMiddleware` (nosniff, `X-Frame-Options: DENY`, `Referrer-Policy:
no-referrer`, x-xss 0, `Cache-Control: no-store`, permissions policy),
`TrustedHostMiddleware` from `REPOLENS_TRUSTED_HOSTS`, and global handlers:
`RequestValidationError → 422 INVALID_INPUT` (with a compatible `detail`
superset), anything unhandled → `500 INTERNAL_ERROR` (traceback logged
server-side only). Error bodies never contain stack traces, paths, env, keys,
or subprocess output.

## 9. Redaction (M-06)

Shared `redact_text`/`clean_text` covers key assignments across `=`, `:`, `:=`
for `api_key`, `password`, `passwd`, `secret`, `token`, `private_key` (with
compound `SECRET_KEY` matched as a unit) plus `AUTHORIZATION` and `COOKIE`
header lines. Labels are preserved as `password=REDACTED`; non-matching lines
are byte-for-byte intact. Applied before every external boundary — Lens context
`render()` (label, file, detail, snippet), review descriptions, structured
logs, and error strings. Lens audits remain metadata-only.

## 10. Concurrency (M-07)

Bounded in-process registry (LRU, ≤256 keys) backing `protected(key)` locks:
ingestion per canonical URL, diff per `(repo, base, head)`, impact per
`(repo, diff, depth)`, review per `(repo, analysis, regenerate)`. Duplicate
concurrent requests serialize and return the winning row; no `IntegrityError`
escapes to the client. Documented limitation: per-process local only — no
distributed queue in scope.

## 11. Input Validation and Intentional Behaviors (M-08, M-09)

`DiffInput.base_revision/head_revision` bounded to 1–100 chars and
`get_file_content path` to 1–2000 chars, yielding `422 INVALID_INPUT` at the
boundary. Intentional behaviors (LIKE wildcard filters, 413-on-limit) are
documented in SECURITY.md and kept unchanged.

## 12. Verification — Tests, Real Repos, Dependencies (M-10)

- **Full backend suite:** `249 passed, 2 skipped, 4 deselected, 1 warning` (the
  16 Phase 9 hardening tests are included), one pre-existing Starlette
  deprecation warning.
- **Hardening suite (`tests/test_security_hardening.py`):** 16 tests — git env/
  argv/output-cap, malicious-hook repo end-to-end pipeline (diff → impact →
  review → lens), redaction incl. `SECRET_KEY :=`, path escape, review/unresolved
  caps, provider byte-cap (fake httpx), headers/request-ID, validation-category
  body, trusted-host rejection, lock serialization, and FK cascade delete.
- **Real repositories (live clones, hardened git):** pallets/flask @
  `d73fa1c…` → 236 files, 1,870,682 B; pallets/itsdangerous @ `672971d…` → 50
  files, 282,547 B; discovery size totals match `git ls-files`; 0 path escapes.
- **Backend deps:** `pip-audit 2.10.1` reported 7 advisories for
  `python-multipart 0.0.9`; upgraded to `0.0.31` → **no known vulnerabilities
  found** (verified re-run). No blind major upgrades performed.
- **Frontend deps:** `npm audit` (npm 11.6.2) → 7 findings (6 moderate, 1
  high): react-router-dom ≤7.17.0 (open redirect via backslash + SSR-hydration
  constructor injection), prismjs, vite→esbuild dev-server read. Exploitability
  in RepoLens: SPA only — no React SSR (`renderToPipeableStream`/
  `react-dom/server` unused), so hydration-injection is not reachable; open
  redirect requires user-initiated navigation to a backslash link (server-side
  path validation still gates content); esbuild advisory is dev-server-only.
  Fixes require breaking major upgrades (react-router-dom 7.18.3, vite 8.2.2)
  and are intentionally **not** auto-applied (no-breaking-changes guard);
  documented as a follow-up.
- **Frontend posture (M-11):** `npx tsc --noEmit` clean; `npm run build`
  (tsc -b && vite build) passes; grep audit confirms no
  `dangerouslySetInnerHTML`/`innerHTML`/`eval`/`window.open`/localStorage in src.
  No frontend test runner exists in the project; render posture is verified by
  static audit + React's escaping, documented rather than blended into a fake
  test count.
- **CI/Docker:** `.github/` and Docker configuration absent by design — recorded
  as N/A, nothing added.

## 13. Performance

Phase 9 adds no new asymptotic work. Git output is capped at 64 MiB and
diff output at 16 MiB; impact/review groups are post-hoc capped with
deterministic truncation; Lens payload rendering is bounded before the HTTP
call with a hard context cap; the lock registry is fixed-size LRU. The
end-to-end malicious-repo workflow completes in ~5 s (in-process suite time),
and the full backend suite runs in ~100 s.

## 14. Limitations

- No authentication/authorization: the tool remains a single-user,
  local-process tool (documented scope).
- Local concurrency only (no distributed queue); multi-process deployments
  need external coordination.
- Frontend dependency advisories (react-router-dom, vite/esbuild) remain open
  pending breaking major upgrades; rationale for low exploitability is
  documented above — revisit when a breaking upgrade can be scheduled.
- Real-repository verification cloned flask/itsdangerous working trees and
  proved acquisition/discovery/safe_join; full diff/impact/review over those
  trees is covered by the instrumented end-to-end hardening workflow, not by a
  live-model Lens run (no provider key — documented test limitation).
- `PRAGMA foreign_keys` is enforced per session by the app's own session
  factory and test override; a future connection path outside the factory is
  responsible for enabling it.
- No claim of absolute security is made; redaction reduces, never guarantees,
  absence of all secrets (e.g. secrets in non-key-looking formats are out of
  pattern).

## 15. Files Changed, Run Instructions and Next Steps

New:

- `backend/app/core/git_safety.py`, `backend/app/core/redaction.py`,
  `backend/app/core/locks.py`, `backend/app/core/security.py`.
- `backend/tests/test_security_hardening.py` (16 tests).
- `SECURITY.md`, `SECURITY_DECISION_MODEL.md`, `PHASE9_REPORT.md`.

Modified:

- `backend/app/core/database.py`, `backend/app/config.py`,
  `backend/app/main.py`, `backend/app/services/ingestion_service.py`,
  `backend/app/repositories/acquisition.py`,
  `backend/app/analysis/diff/{git_service,engine}.py`,
  `backend/app/analysis/search/text_search.py`,
  `backend/app/analysis/impact/engine.py` + `limits.py`,
  `backend/app/analysis/review/{policy,generator,service}.py`,
  `backend/app/ai/{client,context}.py`,
  `backend/app/models/schemas.py`, `backend/app/api/routes.py`,
  `backend/tests/conftest.py` (FK pragma), `backend/requirements.txt`
  (python-multipart 0.0.31), `.env.example`.

Run:

```bash
# Backend (from backend/)
.\\.venv\\Scripts\\python -m pytest tests -q                 # 249 passed, 2 skipped, 4 deselected, 1 warning
.\\.venv\\Scripts\\python -m pytest tests/test_security_hardening.py -q  # 16 passed
.\\.venv\\Scripts\\python -m pip_audit -r requirements.txt   # 0 known vulnerabilities

# Frontend (from frontend/)
npx tsc --noEmit && npm run build
```

Next steps: scheduled frontend dependency upgrades (react-router-dom 7.x, vite
8.x) under a breaking-change window; multi-process coordination if a distributed
deployment is ever scoped; auth when multi-user is scoped; a live-model Lens
end-to-end audit when a provider key is available; CI addition if/when `.github`
is scoped.

---

## Final Response (20 items)

1. **Phase 9 = hardening only.** No new product capabilities were added; every change strengthens an existing Phase 0–8 feature against malicious, oversized, or malformed input.
2. **Git is centralized and hardened.** All git runs through `app/core/git_safety.py`: argv-only, hardened env, forced `-c` overrides (hooks, credential helper, file/ext protocol), 64 MiB output cap, timeouts, process groups on Windows.
3. **Hook execution is proven impossible.** A malicious-fixture repo with a `/.git/hooks` payload survives the full pipeline (clone → diff → impact → review → lens) with hooks never running.
4. **One path-containment enforcement point.** `text_search` now uses `safe_join`; absolute, traversal, UNC, drive, null-byte, and sibling-prefix paths all raise `PathEscapeError`.
5. **Foreign keys are now enforced.** `PRAGMA foreign_keys=ON` runs per session; repository deletion cascades bottom-up through every child table.
6. **Deletion is provably complete.** The end-to-end test confirms zero leftover rows for the deleted repo and no impact on a co-resident repository.
7. **Resource caps truncate loudly.** Impact unresolved/external (500 each), review items (300), Lens evidence (100) and provider bytes (1 MiB) all signal `truncated=true` + reason or a typed error — never silent drops.
8. **HTTP gets request IDs, security headers, and trusted hosts.** X-Request-ID echo + log correlation; nosniff/frame/referrer/cache/permissions headers; TrustedHost allowlist from `REPOLENS_TRUSTED_HOSTS`.
9. **Errors are categorized and leak-free.** 422 `INVALID_INPUT`, 404, 400/413, 502/503 for Lens, otherwise 500 `INTERNAL_ERROR`; bodies never contain stack traces, paths, env, keys, or subprocess output.
10. **Secret redaction is shared and boundary-applied.** Labels preserved (`password=REDACTED`), `:=` and `SECRET_KEY` handled, `AUTHORIZATION`/`COOKIE` covered, applied to Lens render + reviews + logs + errors.
11. **Redaction is tested adversarially.** The workflow test proves committed `password`/`API_KEY`/`SECRET_KEY` values never reach the rendered lens payload.
12. **Concurrency is bounded and idempotent.** LRU-capped in-process locks serialize ingest/diff/impact/review; races return the winning row, never `IntegrityError`.
13. **Schema bounds give clean 422s.** Revisions (1–100) and content-path (1–2000) validated at the boundary.
14. **Backend dependency audit is clean.** `pip-audit 2.10.1`: after `python-multipart 0.0.9 → 0.0.31`, **0 known vulnerabilities**; re-verified.
15. **Frontend findings documented, not hidden.** `npm audit`: 7 findings (react-router-dom SSR/open-redirect, prismjs, vite/esbuild dev-server) — SPA-only usage makes the SSR paths unreachable; fixes require breaking majors and are deferred, not auto-applied.
16. **Real repositories verified.** Live hardened clones of pallets/flask (236 files, 1.87 MB) and pallets/itsdangerous (50 files, 283 KB): discovery totals match `git ls-files`, 0 path escapes.
17. **Full suite green.** `249 passed, 2 skipped, 4 deselected, 1 warning` (the 16 hardening tests included); frontend `tsc --noEmit` + build pass.
18. **CI/Docker absent = documented N/A.** No `.github` or Docker config exists and none was added; intentionally out of scope, recorded.
19. **Decision model is canonical.** `SECURITY_DECISION_MODEL.md` defines the 8 rules (data-not-control, isolation, single enforcement points, loud bounds, error taxonomy, redaction path, idempotency, degraded Lens) that all code and tests must satisfy together.
20. **Honest limitation statement.** No "100% secure" claim; no live-model Lens run (no key); redaction is pattern-based; local-only auth/concurrency; frontend upgrades deferred. Everything above was measured with real tooling and recorded with exact versions and results.