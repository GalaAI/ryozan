# Contributing to Ryozan

Thanks for helping. Set up a local install first with [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Branches and pull requests

1. Branch from `main` with a short descriptive name, such as `fix/booking-deposit-rounding`.
2. Keep each pull request to one change. Say what changed and how you checked it.
3. Run the checks below before you open the pull request. CI runs the same ones.
4. A maintainer reviews and merges. Do not push to `main` directly.

## Checks

API (Python 3.12, with `pip install -r requirements-dev.txt` for ruff):

```bash
cd api
ruff check app/ main.py
python -m unittest discover -s tests -p "*_tests.py"
```

Web app (Node 20+):

```bash
cd app
npm run lint
npm run build
```

If you changed `packages/ryozan-econ`, also run its tests:

```bash
cd packages/ryozan-econ
PYTHONPATH=. python -m unittest discover -s tests -p "*_tests.py"
```

If you change the database schema, add an Alembic migration under `api/alembic`.

## UI comes from `packages/ui` only

All UI components come from `packages/ui` (imported in the app as `@ryozan/ui`). Do not write new components or inline-styled elements in `app/`. Read [`packages/ui/README.md`](packages/ui/README.md) before touching UI.

- No hex colors, px literals or fonts outside the allowlist in app code. `npm run lint:adherence:src` in `app/` checks this.
- A change in `packages/ui/src` reaches the app only after you repack and relock the tarball. Follow [`docs/release/UI_TARBALL_VENDORING.md`](docs/release/UI_TARBALL_VENDORING.md), then run `npm run check:ui-tarball` in `app/`.
- If the package cannot express what you need, open an issue for the design system instead of building a one-off screen.

## Secrets

Never commit real keys or a filled-in `.env`. Copy variable names from `api/.env.example`.

## Security issues

Do not open a public issue for a vulnerability. Follow [`SECURITY.md`](SECURITY.md).
