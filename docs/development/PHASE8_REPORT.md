# Phase 8 Report — Premium UI/UX Refinement + Lens AI Explanation Layer

## 1. Objective

Phase 8 makes RepoLens feel like a serious developer tool and adds an
**optional** "Lens" AI explanation layer that sits strictly on top of the
deterministic findings. The deterministic backend — diff, impact, review, and
investigation — remains the sole source of truth and must be fully functional
without any LLM key. Lens must only annotate evidence RepoLens already proved,
and the UI polish must honor the deterministic-first visual language
(strong/verified results, secondary, neutral, warning-not-danger unresolved,
external muted).

## 2. Scope

- UI refinement: exact landing copy, demo action, Overview "Recent analysis",
  cross-app command palette, Source page with IDE affordances, review progress
  bar, changed-line text inside diff hunks, and states/a11y polish.
- Lens: an OpenAI-compatible LLM explanation layer over four deterministic
  surfaces — change (whole diff or one file), impact, review, and unresolved.
- Context and evidence handling, metadata-only audit logging, error taxonomy,
  prompt-injection posture, and full test coverage — all without requiring a
  real key.

## 3. Requirements Implemented

Backend (all complete and tested):

- `GET /api/repositories/{id}/recent` — last 3 diffs, impact analyses, and
  reviews for the Overview "Recent analysis" strip.
- `POST /api/repositories/{id}/lens/change` — `{diff_id, diff_file_id?}`.
- `POST /api/repositories/{id}/lens/impact` — `{analysis_id}`.
- `POST /api/repositories/{id}/lens/review` — `{review_id}`.
- `POST /api/repositories/{id}/lens/unresolved` — `{analysis_id}`.
- `LensAudit` ORM (repository_id, kind, provider, model, prompt_version,
  context_hash, response_status, error_kind, latency_ms, created_at) — records
  call outcome **without ever storing prompts, snippets, keys, or responses**.
- Changed-line `text` parsed from unified diff payloads and exposed through
  the existing hunks API (no schema migration).

Frontend (all complete and compiling):

- Landing page: exact headline "UNDERSTAND THE CODE BEFORE YOU CHANGE IT.",
  primary "Analyze Repository" ingest, secondary "View Demo / Example" that
  opens the first stored repository, honest disabled state until one exists.
- Overview: real stat tiles plus "Recent analysis" with deep links into the
  Changes / Impact / Review pages.
- Command palette (`Ctrl/Cmd+K`): keyboard-navigable (up/down/enter/esc),
  commands + repository jumping, replaces the previous shortcut placeholder.
- Source page `/repo/:id/source?path&line&range`: breadcrumbs, copy-path,
  read-only indicator, jump-to-line, single-line and range highlight.
- Review: "n / total complete" progress bar in the scorecard.
- Diff hunks now render the changed-line text.
- A `LensPanel` right-drawer with loading / unavailable (`503`) / failed
  (`502`) / data states, evidence cards with row-level deep links, suggested
  checks, and an uncertainty callout. Wired into Changes ("Explain this
  change"), Impact ("Why is this affected?" + "Explain" on Unresolved),
  Review ("Explain this review"), with evidence deep-links into the Source page.

## 4. UI/UX Architecture

- Framer-motion drawer (backdrop + `role="dialog"`, Escape/backdrop close,
  focus moved into the panel, `useReducedMotion` respected).
- Command palette as a top-level overlay owned by `App`, so Cmd+K works on
  the landing page and inside every repository page without duplicating state.
- `SourcePage` uses search params (`path`, `line`, `range`) so Lens evidence,
  palette commands, and future links can deep-link deterministically; the
  underlying `SourceView` gained `highlightRange` without breaking the
  SearchWorkspace embedding.
- States everywhere follow the token palette (`text-primary`, `text-muted`,
  `text-faint`, `line`, `danger/20`, etc.) and the deterministic-first visual
  language: DIRECT = strong, POTENTIAL = secondary, UNRESOLVED = warning,
  EXTERNAL = muted.

## 5. Design System

No new ad-hoc colors were introduced; every Phase 8 component consumes the
existing Tailwind tokens (`ink`, `line`, `accent`, `success`, `warning`,
`danger`, `text-*`). Added: `shadow-panel` for drawers/palette, consistent
`[10px]`-`[11px]` uppercase tracking labels, `kbd` affordance, mono for
identifiers/revisions, amber-tinted uncertainty callouts, and a success-toned
progress bar. Motion is reserved for state transitions and is fully disabled
under `prefers-reduced-motion`.

## 6. Investigation, Changes, Impact and Review UX

- Investigation: unchanged, but source-level evidence now deep-links into the
  Source page when surfaced through Lens evidence.
- Changes: hunks render actual changed-line text; summary strip gains
  "Explain this change" (file-scoped when a file is selected, whole-diff
  otherwise).
- Impact: summary strip gains "Why is this affected?"; the Unresolved section
  header gains a compact "Explain" Lens action.
- Review: scorecard gains a live "n / total complete" progress bar; header
  gains "Explain this review"; all Lens evidence rows carry one-click
  "Open →" links into the exact file + line.

## 7. Lens AI Architecture

Three layers: **determinism** (existing engines prove findings), **evidence
selection** (LensContext builders pick only verified items, bounded), and
**explanation** (an OpenAI-compatible chat call that can only annotate the
selected evidence).

- `app/ai/client.py` — `LLMProvider` ABC + `OpenAICompatibleProvider`
  (httpx, configurable timeout). `get_provider(settings)` returns `None`
  without a key; the key is **never** logged.
- `app/ai/context.py` — `LensContext` with `render()` (hard
  `REPOLENS_LENS_MAX_CONTEXT_CHARS` cap + `evidence_truncated`) and `digest()`
  (sha256 of the rendered payload for the audit). Builders: `build_change_context`
  (whole-diff or file-scoped with hunks), `build_impact_context`,
  `build_review_context`, `build_unresolved_context`.
- `app/ai/prompts.py` — system prompt with named injection defense, bounded
  user prompts, and a strict JSON response contract (summary /
  uncertainty / suggested_checks / evidence_indices, 1-based, max 6).
- `app/ai/service.py` — `LensService.explain()` orchestrates: 404 on missing
  repo/diff/impact/review/file, `503` without a provider, `502` with a
  `kind∈{provider_error,timeout,invalid_response}` on upstream failures, maps
  evidence indices to `LensEvidenceInfo` (fabricated indices dropped), and
  writes the metadata-only `LensAudit` for every attempt.

## 8. Context and Evidence Handling

- Evidence is always a subset of persisted, deterministic data: changed-file
  summaries, scoped hunks with line text, changed-symbols, impact nodes with
  classes + `via` + depth, impact paths (≤ 6), review items/entries, and
  unresolved-call edges.
- Source snippets come from the existing read-source service, are capped at
  `REPOLENS_LENS_MAX_SOURCE_SNIPPET_CHARS` (800), and can push the visibility
  flag to `evidence_truncated` when the hard context cap is hit — the model is
  told the payload was truncated rather than receiving truncated-looking text
  as if it were complete.
- Evidence indices are 1-based and server-mapped; the LLM cannot name files it
  dislikes — it only picks from what the deterministic layers produced.

## 9. Security and Privacy

- No LLM key, prompt, response, snippet, or repository content is persisted.
  `LensAudit` stores only metadata (kind, provider, model, prompt version,
  context hash, response status, error kind, latency).
- Prompt-injection posture: repository content is declared as **data** in the
  system prompt; the model is told that instructions inside repository text are
  not instructions; payloads go in the user role; and response parsing is
  strict (invalid JSON → `502 invalid_response`, never a raw echo).
- `git` and read-source execution follow the same hardened paths as the rest of
  the app; Lens adds no new shell, no new subprocess execution, and no storage
  of untrusted bytes.
- Lens is optional: without a key the API returns `503` and the frontend shows
  a dedicated unavailable state while every deterministic surface keeps
  working.

## 10. API

New endpoints (all under `/api`):

| Endpoint | Purpose | Errors |
| -------- | ------- | ------ |
| `GET /repositories/{id}/recent` | Last 3 diffs / analyses / reviews | `404` repo missing |
| `POST /repositories/{id}/lens/change` | Explain a whole diff (`diff_id`) or one changed file (+`diff_file_id`) | `404`, `503`, `502` |
| `POST /repositories/{id}/lens/impact` | Explain an impact analysis (`analysis_id`) | `404`, `503`, `502` |
| `POST /repositories/{id}/lens/review` | Explain a review (`review_id`) | `404`, `503`, `502` |
| `POST /repositories/{id}/lens/unresolved` | Explain a simulation's unresolved calls (`analysis_id`) | `404`, `503`, `502` |

Response shape: `LensResponse{kind, provider, model, prompt_version, summary,
evidence[], uncertainty?, suggested_checks[]}`. Errors: `503` when no provider
is configured; `502` with `detail` naming the failure kind when the provider
errors, times out, or returns invalid content.

## 11. Testing

Full backend suite: **224 passed, 2 skipped, 4 deselected, 1 pre-existing
Starlette deprecation warning** (up from 202). Phase 8 adds 22 Lens tests in
`tests/test_lens_engine.py`: prompt construction under all caps/coercion/
fences/wrapping; context budget trimming and digest stability; hunk text
parsing; `503` no-provider (`/recent` still `200`); `502` on timeout with an
audit row; `502` on invalid response with an audit row; fabricated evidence
indices dropped; injection-pairing posture; the four endpoint flows including
file-scoped change; all `404` paths; `LensAudit` persistence; and the diff-text
API.

Frontend: `npx tsc --noEmit` is clean and `npm run build` (tsc -b && vite
build) succeeds. A chunk-size warning (~1.1 MB minified JS) is pre-existing and
flagged as a follow-up, not a Phase 8 regression.

## 12. Real Verification Metrics

No claims of real-model accuracy are made: no live key was available, so the
entire Lens pipeline is verified against a fake OpenAI-compatible provider
fixture. Measured with real deterministic data:

| Metric | Value |
| ------ | ----- |
| Backend Lens tests | 22 passing (fake-provider end-to-end) |
| Full backend suite | 224 passed, 2 skipped, 4 deselected |
| Frontend type-check | clean (`tsc --noEmit`) |
| Frontend build | passes (`vite build`) |
| Default Lens bounds | 12,000-char context, 800-char snippets, 6 impact paths in context, 6 evidence selections, 4 suggested checks, 20 s provider timeout |

## 13. Performance

Lens work is bounded by construction: payload rendering is capped before the
HTTP call, the provider call runs inside a timeout (`REPOLENS_LENS_TIMEOUT_SECONDS`,
default 20), and selection caps (6 evidence, 4 checks) keep prompts deterministic
in size. Deterministic surfaces are untouched by Lens — `/recent` is a plain
indexed `SELECT ... LIMIT 3`, and hunk text parsing is linear over the diff
payload. The frontend keeps source files out of React state on the new Source
page and does not animate thousands of graph nodes (existing Impact graph
rendering is preserved unchanged).

## 14. Limitations and Deviations

- Lens is an explainer, not an auditor: it can only annotate findings the
  deterministic layers proved, and its text is generated by an external model
  that may still be imperfect. The deterministic analysis is the source of
  truth.
- OpenAI-compatible chat only; no native Anthropic/other clients (easy to add
  behind the `LLMProvider` ABC if needed).
- No real-model measurements (no key available) — only fixture-verified
  behavior; no accuracy/quality percentages are claimed.
- The production bundle keeps a pre-existing chunk-size warning; code-splitting
  is listed as a follow-up.
- UI polish is structural: tokens, states, keyboard flows, and deep-links are
  consistent and type-checked, but no pixel-level visual regression suite or
  manual browser pass was performed.
- `LensAudit` intentionally stores no prompt/snippet/response, so post-hoc
  redaction is not possible by design.

## 15. Files Changed, Run Instructions and Next Steps

Backend:

- `backend/app/api/routes.py` — `/recent` + 4 Lens endpoints + `_lens_explain`.
- `backend/app/ai/` (new) — `client.py`, `context.py`, `prompts.py`,
  `service.py`, `__init__.py`.
- `backend/app/models/orm.py` — `LensAudit` table.
- `backend/app/models/schemas.py` — Lens request/evidence/response +
  `RecentActivityInfo`; `DiffChangedLineInfo.text`.
- `backend/app/analysis/diff/service.py` — hunk line-text parsing.
- `backend/config.py` — Lens settings (bounds, timeout, prompt version).
- `backend/tests/test_lens_engine.py` (new, 22 tests).

Frontend:

- `frontend/src/components/lens/LensPanel.tsx` (new) — drawer + states +
  evidence cards.
- `frontend/src/components/ui/CommandPalette.tsx` (new) — Cmd+K palette.
- `frontend/src/components/analysis/RecentActivity.tsx` (new) — Overview strip.
- `frontend/src/pages/SourcePage.tsx` (new) — source deep-link page.
- `frontend/src/components/analysis/{ChangeView,ReviewView,ImpactView,SourceView}.tsx`
  — Lens wiring, progress bar, line text, highlight-range/copy/read-only.
- `frontend/src/pages/{LandingPage,IngestionResultPage}.tsx`, `frontend/src/App.tsx`,
  `frontend/src/api/client.ts`, `frontend/src/types/index.ts`.

Run:

```bash
# Backend
cd backend && .\.venv\Scripts\python -m pytest -q                      # 224 passed
# Frontend
cd frontend && npx tsc --noEmit && npm run build
# Optional Lens provider
set REPOLENS_AI_API_KEY=sk-…   # plus optional REPOLENS_AI_BASE_URL / bounds
```

Next steps: code-split the frontend bundle; add native multi-provider clients
behind the provider ABC; a real-model end-to-end audit after a key is
available; a pixel-level visual regression pass; Lens retry/backoff for
provider `429`s; Phase 9 testing/security hardening and Phase 10 deployment +
docs as listed in the README roadmap.