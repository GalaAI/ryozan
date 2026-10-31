# Schema: one path, Alembic

Ryozan's database schema is built and changed by Alembic and nothing else.

- **Fresh install:** `alembic upgrade head` (run from `api/`, with `DATABASE_URL` set).
  The container does this itself before uvicorn starts (`Dockerfile` CMD,
  `railway.toml` startCommand, `docker-compose.yml`). If the migration fails the
  container exits non-zero and the API never starts.
- **Startup issues no DDL.** `main.py` only checks the revision (a loud log line
  if the database is not at head) and runs idempotent data seeds: built-in
  scroll categories, starter quests, default session types, password-hash
  repair, two historical value repairs.
- **Changing the schema:** add a revision under `versions/` whose
  `down_revision` is the current head. Keep one head (`alembic heads`).

## The baseline

`versions/20261031_baseline.py` is the root of the chain. It executes
`baseline/20261031_baseline_schema.sql`, the complete schema exactly as the
pre-2026-10-31 boot path built it (`Base.metadata.create_all` plus ~108
hand-written CREATE/ALTER statements in `main.py`). That SQL is generated, not
written: a schema-only `pg_dump` of a scratch database built by the old boot
path, normalized, and replay-verified by

    python scripts/schema_dump.py <old-boot-db> --write-baseline --verify-url <empty-db>

`baseline/20261031_baseline_schema.json` is the catalog snapshot of the same
database; `scripts/adopt_schema.py` compares live databases against it. The 58
revisions that preceded the baseline were never run in production and are
superseded; they are in git history (last present at 0e7f54d).

The baseline refuses to run on a database that already has Ryozan tables. Such
a database was built by the old boot path and must be adopted instead.

## Adopting a database built by the old boot path

Production, and any local database the old `main.py` built, has every table but
no `alembic_version`. Adopt it once, before the first deploy of this code:

1. **Back up** (schema and data, custom format, restorable with `pg_restore`):

       pg_dump --format=custom --no-owner --no-privileges --file=ryozan-pre-adopt-$(date +%Y%m%d%H%M).dump "$DATABASE_URL"

2. **Dry run** (the default; reads only, changes nothing):

       cd api && DATABASE_URL=... python scripts/adopt_schema.py

   It prints every difference from the baseline: `RECONCILE` (with the exact
   statement it would run), `ACCEPTED`, or `REFUSED` (with the reason). Exit
   code 0 means it can be adopted, 1 means something is refused.

3. **Resolve refusals.** Extra live objects (a table, column, index,
   constraint or trigger the baseline does not have) are never dropped; if
   they should stay, name each one: `--accept extra_column:bttco_users.legacy_flag`.
   A unique index the data violates (for example `ux_bttco_users_email_lower`
   while duplicate emails exist) fails at apply time; run
   `scripts/dedupe_user_emails.py` first. Anything else refused needs a manual
   fix, then a new dry run.

4. **Apply:**

       DATABASE_URL=... python scripts/adopt_schema.py --apply [--accept KEY ...]

   In ONE transaction it runs the listed statements, re-compares (anything
   still different rolls everything back), and stamps `20261031_baseline` with
   Alembic's own stamp. A failure anywhere leaves the database as it was.

5. **Deploy.** The container's `alembic upgrade head` is then a no-op at the
   baseline (or applies any later revisions), and the API starts.

If the code is deployed before adoption, the container's `alembic upgrade head`
refuses (the baseline sees existing tables), the new container exits, the
health check fails, and the previous deployment keeps serving. Adopt, then
redeploy.

### What adoption reconciles automatically

Only metadata changes and changes Postgres validates against the data, all in
the adoption transaction:

| Difference | Statement |
|---|---|
| missing column (nullable, or with a default) | `ALTER TABLE ... ADD COLUMN` with the baseline type/default/nullability |
| column default differs | `ALTER COLUMN ... SET DEFAULT <baseline>` / `DROP DEFAULT` |
| nullability differs | `ALTER COLUMN ... DROP NOT NULL` / `SET NOT NULL` (validated) |
| `character varying(n)` narrower than baseline | `ALTER COLUMN ... TYPE character varying(m)` / `text` (widening only) |
| missing index | the baseline `CREATE [UNIQUE] INDEX` (a unique one is validated) |
| INVALID index (failed `CREATE INDEX CONCURRENTLY`) | `REINDEX INDEX` |
| same index under another name | `ALTER INDEX ... RENAME TO <baseline name>` |
| missing constraint | `ALTER TABLE ... ADD CONSTRAINT` from the baseline definition (validated) |
| same constraint under another name | `ALTER TABLE ... RENAME CONSTRAINT` |

Everything else (missing table, other type changes, changed index/constraint
definitions, identity/generated columns) is refused for a human to decide.
Column order is ignored: a column added later by `ALTER TABLE` sits at the end
of the table, which no query can see.
