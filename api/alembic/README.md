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

The baseline (SQL and snapshot) is history and is never edited, so it keeps the
studio's original `bttco_` names. Everything that describes the CURRENT schema
(the models, `tests/model_schema_parity_pg_tests.py`) compares against a
database at `head`, not against the baseline.

## The bttco -> ryozan rename (`20261031_ryozan_prefix`)

`versions/20261031_ryozan_prefix.py` renames the 61 `bttco_*` tables to
`ryozan_*`, and every constraint and index named after them (144 constraints,
103 indexes, and on Postgres 18 the 496 named NOT NULL constraints). Its
statements are literal `ALTER ... RENAME`s generated, and re-checkable, by

    python scripts/gen_prefix_rename.py <pg18-db-at-20261031_baseline> --check alembic/versions/20261031_ryozan_prefix.py

Renames are catalog-only (no rewrite, no index build, no revalidation) and run
in one transaction, so the database is either entirely old names or entirely
new ones. Each statement takes an ACCESS EXCLUSIVE lock on its table until
commit: well under a second on its own, but it queues behind any open
transaction touching these tables and everything queues behind it.

Production runbook (production is stamped `20261031_baseline` on Postgres 18):

1. **Back up**: `pg_dump --format=custom --no-owner --no-privileges --file=ryozan-pre-rename-$(date +%Y%m%d%H%M).dump "$DATABASE_URL"`.
2. **Rehearse on a restored copy** (a scratch Postgres 18, never production):
   `pg_restore --no-owner --dbname=<scratch-url> ryozan-pre-rename-*.dump`, then
   from `api/` with `DATABASE_URL=<scratch-url>`: `alembic current` (expects
   `20261031_baseline`), `alembic upgrade head`, `alembic check` (expects "No
   new upgrade operations detected."), row counts of a few tables equal the
   backup's, `pg_dump --schema-only` shows no `bttco_` object name (the column
   `bttcoin_balance` stays), and `alembic downgrade 20261031_baseline` followed
   by `alembic upgrade head` succeeds. A RENAME of a name the restored copy
   does not have (drift since the baseline) fails here, atomically, instead
   of in the deploy.
3. **Deploy.** The container runs `alembic upgrade head` before uvicorn, which
   applies the rename; the previous container keeps serving until the new one
   is healthy. Deploy at a quiet moment: requests the old container serves
   between the commit and the switch fail on the renamed tables, and a
   long-running transaction delays the migration (the new container stays
   unhealthy, the old one keeps serving, nothing is half-renamed).
4. **Roll back** if needed: `alembic downgrade 20261031_baseline` (exact
   inverse), then redeploy the previous build.

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
   `scripts/dedupe_user_emails.py` first (it addresses the current `ryozan_`
   names, so on a legacy `bttco_` database run it from commit 16f1746, the last
   one before the rename). Anything else refused needs a manual fix, then a new
   dry run. Adoption compares against the baseline, so its keys and names are
   the baseline's `bttco_` ones.

4. **Apply:**

       DATABASE_URL=... python scripts/adopt_schema.py --apply [--accept KEY ...]

   In ONE transaction it runs the listed statements, re-compares (anything
   still different rolls everything back), and stamps `20261031_baseline` with
   Alembic's own stamp. A failure anywhere leaves the database as it was.

5. **Deploy.** The container's `alembic upgrade head` then applies every later
   revision (including `20261031_ryozan_prefix`, the rename) and the API starts.

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
