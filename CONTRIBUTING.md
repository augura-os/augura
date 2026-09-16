# Contributing to Augura

> This is the single source of truth for the development workflow.

---

## 1. Branching (trunk-based)

- `main` is the only long-lived branch and is always releasable (CI green).
- All changes go through short-lived `feature/<scope>-<desc>` branches (e.g. `feature/graph-similar-to`).
- Merge with **Squash merge**, delete the branch afterwards.
- Never push directly to `main` — branch protection with required status checks is enabled.

## 2. Commit conventions

```
type(scope): subject

feat(graph): SIMILAR_TO observation-pair edges
fix(api): coerce string list fields from JSON Mode providers
```

- type: `feat` / `fix` / `perf` / `docs` / `chore` / `test` / `refactor`
- One commit does one thing; a docs change that belongs to a code change may share the commit.

## 3. Pull requests

1. Before opening a PR, make sure `pytest`, `vitest run`, `ruff check` and `npm run build` are all green.
2. PR description, four sections: motivation / changes / verification / rollback.
3. A human reviews and merges.

### Hard review rules

- **Human > AI**: human corrections to tags, clusters and merges always win, and must leave an `edit_logs` trail.
- Never silently weaken the review queue, the calibration loop, or the audit trail.

## 4. Testing

- Backend: `apps/api/tests/` (pytest). Pure-logic unit tests + Postgres integration tests (separate test databases; skipped automatically when no DB is reachable).
- Frontend: `apps/web/src/**/*.test.ts` (vitest).
- Cover critical paths; don't chase coverage numbers. New features ship with critical-path tests; bug fixes start with a reproduction test.
- Database migrations must be downgrade-able; the upgrade→downgrade→upgrade round-trip in `test_migrations.py` is a CI gate.

## 5. Releases & rollback

- Semantic tags: `vX.Y.Z`, pushed to the repo. Docker images are built from tags by the publish workflow.
- Rollback options, pick per scenario: revert PR / `alembic downgrade` / restore the latest pg_dump.

## 6. CI

`.github/workflows/ci.yml` runs on PRs and pushes to `main`:

- backend: ruff → Postgres 16 service → alembic upgrade → pytest
- frontend: npm ci → tsc -b → vitest → vite build

Red CI means no merge.
