# RepoLens — Demo Checklist

Before showing RepoLens to a recruiter or reviewer:

---

## Environment

- [ ] Python 3.12 installed
- [ ] Node 18+ installed
- [ ] Backend dependencies installed (`cd backend && pip install -r requirements.txt`)
- [ ] Frontend dependencies installed (`cd frontend && npm install`)

## Backend

- [ ] Backend starts without errors (`cd backend && uvicorn app.main:app --reload --port 8000`)
- [ ] Health endpoint works (`curl http://localhost:8000/api/health` returns `{"status":"ok"}`)
- [ ] API docs accessible at `http://localhost:8000/docs`

## Frontend

- [ ] Frontend starts without errors (`cd frontend && npm run dev`)
- [ ] Landing page loads at `http://localhost:5173`
- [ ] No console errors on landing page

## Full Workflow

- [ ] Real repository ingestion works (paste a GitHub URL, wait for ready)
- [ ] Overview shows correct file count and language shares
- [ ] Search returns ranked results
- [ ] Investigation shows neighborhood with evidence
- [ ] Changes page: real diff produces hunks and changed symbols
- [ ] Impact page: simulation produces DIRECT/POTENTIAL/TESTS nodes
- [ ] Review page: checklist generated with REQUIRED/RECOMMENDED/INFORMATIONAL
- [ ] Lens no-provider behavior shows clear "not configured" state
- [ ] No broken navigation links
- [ ] No blank screens or error states without explanation

## Documentation

- [ ] README is current (roadmap complete, status accurate)
- [ ] DEPLOYMENT.md exists and is accurate
- [ ] SECURITY.md exists and is accurate
- [ ] API documentation in README matches actual endpoints
- [ ] No stale "coming soon" or "not implemented" text in production code

## Code Quality

- [ ] `npm run check` passes (backend tests + frontend typecheck + build)
- [ ] No console.log in frontend source
- [ ] No print statements in backend production code
- [ ] No TODO/FIXME in production code (only in .venv, node_modules, cloned repos)

## Quick Reset

If something goes wrong during the demo:

1. Stop the backend
2. Delete `backend/data/` (DB + cloned repos)
3. Restart the backend — DB rebuilds idempotently on startup
4. Re-ingest a repository