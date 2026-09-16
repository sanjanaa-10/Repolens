# Phase 10 Report — Deployment + Documentation

## 1. Objective

Phase 10 closes the roadmap by making RepoLens **operable in production**
against the already-hardened Phase 9 codebase, and by bringing the project
documentation (README, security model, deployment guide) up to date. Scope
discipline is unchanged: no new product capabilities, no new attack surface.

Per the audited scope in SECURITY.md / PHASE9_REPORT.md, RepoLens is a local,
single-user tool and **Docker/Kubernetes deployment and CI are out of scope**
(document N/A). Phase 10 therefore does **not** add a Dockerfile or `.github`
CI; it provides a reverse-proxy + TLS reference deployment for the topology
that actually exists, plus operations guidance.

## 2. Deliverables

**New files**

- `DEPLOYMENT.md` — the production/operations guide: topology, prerequisites,
  build steps, environment configuration, run commands, frontend serving +
  reverse proxy, backups & data layout, operational checks, and security
  reminders.
- `deploy/Caddyfile.example` — reverse-proxy reference with automatic TLS,
  static `frontend/dist` serving, SPA fallback, `/api/*` proxy to the backend,
  and a host-must-match `REPOLENS_TRUSTED_HOSTS` reminder.
- `deploy/nginx.example.conf` — nginx equivalent (HTTP→HTTPS redirect, TLS
  cert paths, `try_files` SPA fallback, `/api/` proxy, security headers).
- `deploy/repolens.service.example` — Linux systemd unit running uvicorn bound
  to `127.0.0.1:8000` with a private tmp and a ReadWrite data path.
- `deploy/env.production.example` — production `backend/.env` template with
  the must-set `REPOLENS_TRUSTED_HOSTS`, `REPOLENS_DEBUG=false`, persistent
  storage paths, and optional Lens settings.

**Updated files**

- `README.md` — roadmap marks Phases 9 and 10 complete; Status headline
  updated; a Phase 9 and a Phase 10 capability bullet added; Phase 9/10
  limitations section added; architecture diagram now lists the Phase 9 core
  modules and the current test count (249); a production pointer to
  `DEPLOYMENT.md` added to Quick start.
- `package.json` (root) — a `check` script that runs `test:backend` then the
  frontend `typecheck` + `build`.

## 3. Production topology (documented in DEPLOYMENT.md)

```
Internet → [ reverse proxy + TLS (Caddy/nginx) ]
            ├─ /api/*  →  uvicorn backend 127.0.0.1:8000
            └─ static  →  frontend/dist (SPA, /index.html fallback)
```

- `REPOLENS_TRUSTED_HOSTS` must list the proxy's public hostnames or
  `TrustedHostMiddleware` rejects the proxied requests.
- `REPOLENS_DEBUG=false` in production.
- Data lives under `backend/data/` (SQLite `repolens.db` + clone working
  trees); only the DB is backup-critical (working trees are re-cloneable).

## 4. Verification

- **Backend suite:** `249 passed, 2 skipped, 4 deselected, 1 warning` (no
  behavior change — Phase 10 is documentation/config
  only).
- **Frontend typecheck + build:** `npx tsc --noEmit` clean; `npm run build`
  (`tsc -b && vite build`) succeeds.
- **Config references:** the deployment configs were authored to match the
  real runtime — static `frontend/dist` (verified it exists: `index.html`,
  `assets/`, `repolens.svg`), the `/api` prefix from `app/main.py`, and the
  `REPOLENS_TRUSTED_HOSTS` env var already honored by `TrustedHostMiddleware`.

## 5. Scope

In scope: production documentation, reference reverse-proxy/service/env
configs, and README/documentation updates.

Out of scope (documented N/A, none added): Docker, Kubernetes, CI (`.github`),
authentication/authorization, multi-user, distributed workers.

## 6. Next steps / ongoing items

- Frontend dependency advisories (react-router-dom, prismjs, vite/esbuild)
  remain open pending breaking major upgrades — see PHASE9_REPORT.md §12.
- A live-model Lens end-to-end audit when a provider key is available.
- The reference deployment configs must be adapted per host (hostname, cert
  paths, service user); they are illustrative, not turnkey.