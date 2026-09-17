# RepoLens Security

RepoLens is a local single-user developer tool that ingests a public GitHub
repository, deterministically analyzes it (symbols, diffs, impact, review), and
optionally explains findings through an LLM (`Lens`). This page describes how
the tool is hardened, what it intentionally does not do, and how to operate it
safely.

## Trust model

- Repository code, git history, file names, and commit messages are **data**.
  They are never executed as shell, never rendered as HTML by the frontend, and
  never treated as instructions by the model.
- The product's outputs (diff, impact, review, Lens) are advisory. The
  deterministic analysis is the source of truth; Lens can only annotate
  evidence the deterministic layers already proved.
- All persisted rows are scoped by `repository_id`; there is no cross-repository
  read path by design.
- Exactly two outbound flows exist: `git clone`/`git fetch` of a validated
  public GitHub HTTPS URL, and the optional Lens HTTP POST to the configured
  OpenAI-compatible endpoint. Everything else is loopback.

## Hardening summary (Phase 9)

| Area | What is enforced |
| --- | --- |
| Git execution | Every git subprocess runs through `app/core/git_safety.py`: argument arrays only (never a shell), a hardened environment (ambient `GIT_*`/SSH variables stripped, terminal prompt disabled, system/global config disabled), mandatory `-c` overrides (`core.hooksPath=`, `credential.helper=`, `protocol.file.allow=never`, `protocol.ext.allow=never`), a 64 MiB output cap, timeouts, and process-group creation on Windows. |
| Revision/similar inputs | Validated with an allowlist (no leading `-`, no shell metacharacters, no traversal, bounded length) before they reach git as individual argv entries. |
| Path containment | All file reads use the single `safe_join`/`safe_relative_path` enforcement point (`PathEscapeError` on traversal, absolute paths, drive letters, UNC, slash/backslash mixing, null bytes). Symlinks are skipped during discovery. |
| Resource limits | Clone timeout and size caps, per-file size caps, diff/impact/relationship/review/Lens caps. Limits surface as controlled errors or `truncated=true` + `truncate_reason` — never silent drops. |
| Database | `PRAGMA foreign_keys=ON` on every session; repository deletion cascades through every child table; all queries are parameterized. |
| HTTP | TrustedHostMiddleware allowlist, security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `X-XSS-Protection`, `Cache-Control`, `Permissions-Policy`), request-ID correlation, and global error handlers that return categorized bodies with no stack traces, paths, env, or secrets. |
| Secrets | A shared redaction layer covers `api_key`/`password`/`passwd`/`token`/`secret`/`private_key`/`SECRET_KEY` assignments plus `AUTHORIZATION`/`COOKIE` header lines. It is applied to Lens context rendering, review descriptions, logs, and error text. Lens audits store metadata only (no prompts, snippets, responses, or keys). |
| Concurrency | In-process asyncio locks key ingestion (per canonical URL), diff, impact, and review work so races return the winning row instead of `IntegrityError`. |
| Lens/LLM | No provider configured → `503`; provider errors/timeouts/malformed responses → `502` with a stable `kind`; fabricated evidence indices dropped server-side. |

## Intentional behaviors (review before deploying)

- **LIKE wildcards in filter searches.** `list_symbols`/`list_imports` build
  `ilike` patterns from the `file` filter. Values are bound parameters (no SQL
  injection), but `%` and `_` act as wildcards intentionally.
- **413 on limit violation.** Resource-limit violations return `413` with the
  limit name, not `400`. This is deliberate so clients can distinguish "your
  input was invalid" from "your input exceeded a protective cap."

## Operating guidance

- Run the backend bound to `127.0.0.1` for local use. If you deploy it, set
  `REPOLENS_TRUSTED_HOSTS` to your public hostnames and put TLS + a reverse
  proxy in front; do not expose the raw backend to the internet.
- Do not paste the LLM provider API key into repository files or commit it. It
  is read from the environment only and never logged.
- The frontend is a static Vite build; serve it over HTTPS in production. The
  dev server (`vite dev`) is a development tool — bind it to localhost.
- Lens is optional. Without `REPOLENS_AI_API_KEY` the deterministic core is
  fully functional and Lens reports `503 unavailable`.

## Scope (what RepoLens is not)

No authentication or authorization, multi-user support, OAuth, private
repositories, code execution, background workers, Redis/Kubernetes/Docker
deployment, or vulnerability scanning are implemented or planned in the current
scope. Secret data *inside an analyzed repository* is redacted at
external boundaries but is not a substitute for removing credentials from the
source repository.

## Reporting

This is a local, single-user research tool. There is no public security
contact. For questions or concerns about the hardening described here, open an
issue against the project repository. Do not submit credentials, prompts, or
repository contents as part of any report.