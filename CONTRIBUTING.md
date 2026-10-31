# Contributing to Ryozan

Thanks for helping. Set up a local install first with [`docs/QUICKSTART.md`](docs/QUICKSTART.md). [`ROADMAP.md`](ROADMAP.md) lists the work that is next.

## Branches and pull requests

1. Branch from `main` with a short descriptive name, such as `fix/booking-deposit-rounding`.
2. Keep each pull request to one change. Say what changed and how you checked it.
3. Run the checks below before you open the pull request.
4. A maintainer reviews and merges. Do not push to `main` directly.

## CI

CI runs on GitHub Actions for every pull request and every push to `main`:

- [`api.yml`](.github/workflows/api.yml): Backend Verify (ruff lint and format, unit tests), Schema boot (a real Postgres built with `alembic upgrade head`, plus the Postgres tests) and the `ryozan-econ` package tests.
- [`app.yml`](.github/workflows/app.yml): Frontend Verify (UI tarball check, design adherence lint, ESLint, build).

## Checks

API (Python 3.12, with `pip install -r requirements-dev.txt` for ruff):

```bash
cd api
ruff check app/ main.py
ruff format --check app/ main.py
python -m unittest discover -s tests -p "*_tests.py"
```

Tests in files ending in `_pg_tests.py` need a Postgres database. They skip unless the variables named at the top of each file are set, such as `RYOZAN_TEST_DATABASE_URL`.

Web app (Node 20+):

```bash
cd app
npm run check:ui-tarball
npm run lint:adherence:src
npm run lint
npm run build
```

If you changed `packages/ryozan-econ`, also run its tests:

```bash
cd packages/ryozan-econ
PYTHONPATH=. python -m unittest discover -s tests -p "*_tests.py"
```

## Database schema: Alembic only

Alembic is the only way the schema is built or changed. The API issues no DDL at startup. Read [`api/alembic/README.md`](api/alembic/README.md) first.

- Every schema change needs a new revision in `api/alembic/versions/` whose `down_revision` is the current head. Keep one head (`alembic heads`). CI fails on two.
- Change the SQLAlchemy model and add the revision in the same pull request. CI checks that every model column exists in a database at head.
- Never edit the baseline revision or its SQL and snapshot files. They are history.
- Do not add `create_all` calls or raw `CREATE` or `ALTER` statements to `main.py`.

## Settings and env vars

- Every setting lives in [`api/app/core/config.py`](api/app/core/config.py). Add a new one there and to [`api/.env.example`](api/.env.example) with a safe default.
- Env vars that once started with `BTTCO_` now start with `RYOZAN_`. The old names still work and log a warning ([`legacy_env.py`](api/app/core/legacy_env.py)). Use the `RYOZAN_` names.
- A new optional integration stays off until its keys are set, and never returns a key or secret to the client. Follow [`integrations.py`](api/app/api/v1/endpoints/integrations.py).

## UI comes from `packages/ui` only

All UI components come from `packages/ui`, imported in the app as `@ryozan/ui`. Do not write new components or inline-styled elements in `app/`. Read [`packages/ui/README.md`](packages/ui/README.md) before touching UI. The design rules are also a Claude Code skill: [`.claude/skills/ryozan-design-canon/SKILL.md`](.claude/skills/ryozan-design-canon/SKILL.md).

- No hex colors, px literals or fonts outside the allowlist in app code. `npm run lint:adherence:src` in `app/` checks this.
- A change in `packages/ui/src` reaches the app only after you repack and relock the tarball: `npm run pack:app` in `packages/ui`, then `npm install --package-lock-only "@ryozan/ui@file:vendor/ryozan-ui-<version>.tgz"` in `app/`. Details are in [`docs/release/UI_TARBALL_VENDORING.md`](docs/release/UI_TARBALL_VENDORING.md). `npm run check:ui-tarball` fails if you skip it.
- If the package cannot express what you need, open an issue for the design system instead of building a one-off screen.

## Secrets

Never commit real keys or a filled-in `.env`. Copy variable names from `api/.env.example`.

## Security issues

Do not open a public issue for a vulnerability. Follow [`SECURITY.md`](SECURITY.md).
