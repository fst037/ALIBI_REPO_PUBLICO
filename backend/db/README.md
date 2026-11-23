# ALIBI database — schema & migrations

The **control-plane** database (a single plain-RDS PostgreSQL instance).
This plane holds ALIBI's *own* data: users, dataset **metadata**, inferred
schemas, conversations/messages, saved charts/queries, billing and
notifications.

> The **contents** of uploaded files are NOT here. They live as Parquet in S3
> and are queried with DuckDB (the two-data-planes design). What this database stores for an uploaded file is its
> metadata + inferred schema + an S3 pointer (`datasets.storage_key`).

## The source of truth is `app/db/models.py`, not the console

Schema management is **Alembic** (`backend/alembic/`) generating migrations
from `backend/app/db/models.py` (SQLModel) — never hand-typed `CREATE TABLE`/
`ALTER TABLE` in a psql session, never a hand-written SQL migration file. The
psql session (over the SSM tunnel) is only for
exploring data, not for authoring schema.

There used to be hand-written SQL files under `db/migrations/` — they're
gone. Their content lives on as the first two Alembic revisions:
`alembic/versions/0001_baseline.py` (a raw-SQL revision reproducing the
original schema exactly, kept as raw SQL because it predates Alembic and
there was no live DB to autogenerate against) and
`0002_conversation_favorite.py` (a normal `op.add_column` revision — the
pattern every migration since follows). See the top of `0001_baseline.py`
for the full backstory, including a real gotcha worth knowing: the shared
dev DB's 0002 had been applied by hand at some point but never recorded
anywhere, and `\dt` alone didn't catch it (it only shows table names, not
column-level drift) — `alembic upgrade head` did, by failing loudly with
`DuplicateColumn`. Trust Alembic's own `alembic_version` tracking over
manual `\dt` inspection.

## Adding or changing a table

1. Edit `app/db/models.py` — add/modify the `SQLModel` class(es).
2. Generate a migration from the diff:
   ```bash
   alembic revision --autogenerate -m "add foo table"
   ```
3. **Read the generated file in `alembic/versions/` before trusting it** —
   autogenerate misses renamed columns and some constraint tweaks.
4. Apply it (see below), then commit the migration file alongside the model
   change in the same PR.

## Applying a migration

You need a path to the private RDS instance — use the SSM bastion
(`terraform/modules/bastion`) (browser Session Manager,
or a local `aws ssm start-session` port-forward). With `ALIBI_DATABASE_URL`
set (see `backend/.env.example`, pointed at the tunnel's local port) and
`alembic`/`sqlmodel` installed (`backend/requirements.txt`), from `backend/`:

```bash
alembic upgrade head
```

To sanity-check a migration without touching a database, render the SQL
instead of connecting:

```bash
alembic upgrade head --sql
```

If `alembic upgrade head` ever fails with an "already exists" error on a
shared database: someone applied that change by hand (or via a different
session) without it being recorded. Don't fight it — `alembic stamp head`
records the DB as caught up, no DDL needed, since the schema already matches.

### ⚠️ Coordinating on a shared database

The dev DB is **shared** between developers. A migration should be applied
**once** (whoever writes it runs it), not independently by each dev. Alembic
`alembic current` tells you what's actually stamped on the DB you're pointed
at — check it before assuming you need to run something.

## Conventions (`app/db/models.py` is the enforcement point)

- Single `public` schema; `text` primary keys (app-generated, readable ids
  matching the contract); `timestamptz` for real instants (display strings
  like "hace 12 min" are formatted by the backend from these).
- `JSONB` only for the genuinely semi-structured, read-as-a-whole blobs:
  polymorphic chat messages, the embedded `QueryResult`, plan features/limits.
- Native `ENUM` types mirror the Pydantic enums in `backend/app/schemas/`
  (reused directly by `app/db/models.py` where one already exists).
- Multi-tenant: everything a user owns carries an owner/user FK.
- Keep `app/db/models.py` in lock-step with `backend/app/schemas/*.py` — they
  describe the same shapes on two sides of the wire (the model is the DB
  shape, the schema is the wire shape; they often — not always — coincide).
- Scope: `app/db/models.py` covers only the SYSTEM-wide tables above. Module
  B's per-tenant ingested data is **not** modelled here by design, and it is
  no longer undecided: it lives as Parquet in S3 (or local disk), queried
  with DuckDB. This file only
  ever models the control plane (uploads/datasets/folders *metadata*, schema,
  embeddings) — `Upload`, `Dataset`, `DatasetSchema`, `DatasetColumn`,
  `Folder`, `FolderDataset`, `DatasetColumnEmbedding` in `app/db/models.py`.
