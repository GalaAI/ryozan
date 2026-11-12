---
applyTo: "api/**"
---

# api/ — FastAPI backend rules

These rules supplement `.github/copilot-instructions.md` and override defaults
when editing anything under `api/`.

## Schema changes: Alembic only
The schema has one path. The container runs `alembic upgrade head` before
uvicorn starts and `main.py` issues no DDL. When you add or change a column,
table, index or constraint on a model in `api/app/models/`:

1. Write an Alembic revision in `api/alembic/versions/YYYYMMDD_<slug>.py`
   whose `down_revision` is the current head (`alembic heads` must stay at one).
2. Never add CREATE/ALTER statements to `main.py`; startup only seeds data.
3. A column added to a table that already has rows must be nullable or carry
   a server default.

See `api/alembic/README.md` (baseline, adopting an old database).

## SQL style
* Startup data seeds (`_run_startup_seeds` in `main.py`) run on every boot, so
  every seed statement must be idempotent (`ON CONFLICT`, guarded `UPDATE`).
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
* `ruff==0.17.4`, `line-length=120`, configured in `api/ruff.toml`.
* Run from `api/`: `ruff check app/ main.py && ruff format --check app/ main.py`.
