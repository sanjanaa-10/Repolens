# RepoLens — Resume Metrics

Verified, reproducible metrics from the final implementation. All numbers
come from real-repository testing with the full API pipeline.

---

## Repository Ingestion

| Metric | Value |
| --- | --- |
| Real repositories tested | pallets/flask, pallets/itsdangerous, psf/requests, urllib3/urllib3 |
| Total files ingested | 599 (236 + 50 + 130 + 183) |
| Supported ingestion targets | Public GitHub HTTPS repositories |
| Clone method | Depth-1 shallow clone, safe subprocess execution |

---

## Parsing (AST + Symbol Extraction)

| Metric | Value |
| --- | --- |
| Supported languages | Python, JavaScript, TypeScript (including JSX/TSX) |
| Parser technology | Tree-sitter (deterministic, offline) |
| Extraction | Top-level + nested symbols, imports, exports with exact source locations |
| Repository execution | Never — purely syntax-level |

---

## Relationship Engine

| Metric | Flask | itsdangerous |
| --- | --- | --- |
| Total relationships | 7,245 | 1,080 |
| Resolved edges | 3,277 | 678 |
| External edges | 349 | 51 |
| Unresolved edges | 3,619 | 351 |
| Edge types | 7 supported by the engine; 6 observed in the analyzed repositories |

---

## Git Diff Analysis

| Metric | Flask | itsdangerous |
| --- | --- | --- |
| Commits compared | 514fc6b → d73fa1c | real diff |
| Changed files | 8 | 11 |
| Changed lines | +43 / -12 | +99 / -44 |
| Changed symbols | 19 | 4 |
| Analysis time | ~1.2 s | ~1.4 s |

---

## Change Impact Simulation

| Metric | Value |
| --- | --- |
| Changed symbols | 19 |
| Direct impact | 27 |
| Potential impact | 97 |
| Test impact | 5 |
| Unresolved | 215 |
| External | 7 |
| Total nodes | 351 |
| Impact paths | 102 |
| Truncated | false |
| Analysis time | 0.04 s |

---

## Change Review

| Metric | Value |
| --- | --- |
| Review items | 31 |
| Required | 24 |
| Recommended | 6 |
| Informational | 1 |
| Generation time | ~70.6 ms |

---

## Testing

| Metric | Value |
| --- | --- |
| Backend test suite | 249 passed / 2 skipped / 4 deselected / 1 warning |
| Skipped | 2 (Windows-specific) |
| Deselected | 4 (network/integration) |
| Security hardening tests | 16 |
| Frontend typecheck | Clean (TypeScript) |
| Frontend build | Passes (Vite production build) |

---

## Lens AI

| Metric | Value |
| --- | --- |
| Feature status | Implemented and tested |
| Provider verification | Tested with fake OpenAI-compatible provider |
| Live provider verification | Environment-dependent (no provider key in test env) |
| Deterministic core | Fully functional without any LLM key |
| Evidence bounding | 12,000 char context cap, 800 char source snippets, 6 impact paths |

---

## Security (Phase 9)

| Metric | Value |
| --- | --- |
| Hardening checklist items | 41/41 complete |
| Security tests | 16 |
| Mitigations implemented | 11 (M-01 through M-11) |
| Dependency audit (Python) | 0 known vulnerabilities |
| Dependency audit (JS) | 7 findings (6 moderate, 1 high) — all documented, low exploitability |

---

## Deployment

| Metric | Value |
| --- | --- |
| Production topology | Static frontend + reverse proxy (TLS) + loopback backend |
| Reference configs | Caddy, nginx, systemd, production env |
| Deployment documentation | DEPLOYMENT.md |

---

## Architecture

| Metric | Value |
| --- | --- |
| Backend | Python 3.12 + FastAPI + SQLAlchemy + SQLite + tree-sitter |
| Frontend | React 18 + TypeScript + Vite 5 + Tailwind CSS |
| API endpoints | 33 (including health) |
| Phases completed | 0–10 (11 phases) |