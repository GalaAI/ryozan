---
applyTo: "api/**"
---

# api/ — FastAPI backend rules

These rules supplement `.github/copilot-instructions.md` and override defaults
when editing anything under `api/`.

## Migration trap reminder
When you add or rename a column on any SQLAlchemy model in `api/app/models/`:

1. Write the alembic migration in `api/alembic/versions/YYYYMMDD_<slug>.py`.
2. **Also add the matching idempotent SQL to `_run_startup_migrations()` in
   `api/main.py`.** Railway doesn't run alembic on deploy.
3. PR description must include the SQL you added to `main.py`.

## SQL style
* Use `IF NOT EXISTS` on every `ALTER TABLE ADD COLUMN`, `CREATE TABLE`,
  `CREATE INDEX`. The startup migration runs on every boot.
* Use `text(...)` from sqlalchemy when running raw SQL inside async sessions;
  asyncpg connections accept raw strings.

## Async safety
* Every endpoint is `async def`. Every DB call uses
  `async with async_session() as db:` or the FastAPI `Depends(get_db)`
  injected `db: AsyncSession`. Never call sync sqlalchemy methods.
* Background tasks that touch the DB MUST open their own session — don't
  reuse the request session past `await`.

## Auth
* `api/app/api/v1/endpoints/auth.py` is load-bearing. Any change here needs a
  manual probe (`curl POST /api/v1/auth/login`) in the PR description.
* Password hashing is bcrypt via `app.core.security.hash_password` /
  `verify_password`. Don't import passlib — we use raw `bcrypt`.

## Error responses
* Validation failures → 422 (let pydantic raise).
* Bad credentials → 401 with `detail="Invalid email or password"`.
* Missing record → 404 with a useful `detail`.
* Forbidden role → 403 with a useful `detail`.
* Never `raise HTTPException(500, ...)` — let unexpected exceptions surface
  as real 500s so Railway logs them.

## Lint (enforced)
* `ruff==0.15.13`, `line-length=120`, configured in `api/ruff.toml`.
* Run from `api/`: `ruff check app/ main.py && ruff format --check app/ main.py`.
