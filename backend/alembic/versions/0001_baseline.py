"""baseline: reproduce the original hand-applied schema (formerly 0001_initial_schema.sql)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-27

This is a BASELINE revision, not a normal Alembic-generated one: it exists so
Alembic has a starting point that matches the schema already applied by hand,
back when schema changes were plain SQL files applied manually over psql
(see backend/db/README.md — that file, `0001_initial_schema.sql`, no longer
exists in the repo; this revision is its full replacement/successor, reproduced
here as raw SQL since it predates Alembic and there was no live DB to
autogenerate a diff against). It deliberately does NOT include what was
`0002_conversation_favorite.sql` — that's its own revision (0002_conversation_
favorite.py).

That split turned out to matter in practice (2026-07-27): `\dt` over the SSM
tunnel only confirmed table *count* (15, matching 0001), which looked like
0002 had never run — but attempting `alembic upgrade head` on the shared dev
DB failed with `DuplicateColumn: conversations.favorite already exists`.
0002 HAD been applied by hand at some point, just never recorded anywhere
(no tracking table existed before Alembic). The failed upgrade rolled back
cleanly (transactional DDL); the fix was `alembic stamp head` directly
(record-keeping only, no real DDL needed, since the schema already matched).
Lesson: don't infer full schema state from `\dt` alone — table names don't
capture column-level drift. This is exactly the class of problem Alembic's
own version tracking (the `alembic_version` table) exists to prevent going
forward.

- On the EXISTING dev database: `alembic stamp 0001_baseline`, then attempt
  `alembic upgrade head` — if it fails with a "such-and-such already exists"
  error, that means the target state was already reached by hand at some
  point; re-stamp directly to `head` instead (no real DDL needed). If it
  succeeds cleanly, the upgrade was genuinely needed and is now done.
- On a FRESH database: `alembic upgrade head` runs both revisions in order,
  no surprises.

Everything from here forward is a normal `alembic revision --autogenerate`
against `app/db/models.py`.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_UPGRADE_SQL = """
-- ---------------------------------------------------------------------------
-- Enum types (closed value sets from the Pydantic enums)
-- ---------------------------------------------------------------------------
CREATE TYPE file_type            AS ENUM ('csv', 'xlsx', 'pdf');
CREATE TYPE dataset_status       AS ENUM ('processed', 'processing', 'error');
CREATE TYPE column_type          AS ENUM ('Texto', 'Número', 'Número entero', 'Fecha', 'Porcentaje', 'Booleano');
CREATE TYPE inference_confidence AS ENUM ('alta', 'media', 'baja');
CREATE TYPE chart_type           AS ENUM ('barras', 'lineas', 'dispersion', 'torta', 'histograma', 'calor', 'tabla');
CREATE TYPE message_role         AS ENUM ('user', 'assistant', 'guard', 'system');
CREATE TYPE plan_id              AS ENUM ('free', 'pro', 'business');
CREATE TYPE billing_cycle        AS ENUM ('monthly', 'annual');
CREATE TYPE invoice_status       AS ENUM ('paid', 'pending', 'failed');
CREATE TYPE ledger_action        AS ENUM ('query', 'visualization');
CREATE TYPE notification_tone    AS ENUM ('ok', 'warn', 'err', 'info');

-- Sets updated_at on every UPDATE for the tables that carry it.
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Plans (reference/config — seeded at the bottom, mirrors plans fixture)
-- ---------------------------------------------------------------------------
CREATE TABLE plans (
  id            plan_id      PRIMARY KEY,
  name          text         NOT NULL,
  price_monthly numeric(10,2) NOT NULL,
  price_annual  numeric(10,2) NOT NULL,
  recommended   boolean      NOT NULL DEFAULT false,
  features      jsonb        NOT NULL DEFAULT '[]'::jsonb,
  limits        jsonb        NOT NULL
);

-- ---------------------------------------------------------------------------
-- Identity / account (auth + account domains; entity is User)
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  id                 text        PRIMARY KEY,
  email              text        NOT NULL UNIQUE,
  name               text        NOT NULL,
  workspace          text,
  password_hash      text,
  email_verified     boolean     NOT NULL DEFAULT false,
  two_factor_enabled boolean     NOT NULL DEFAULT false,
  theme              text,
  product_emails     boolean     NOT NULL DEFAULT true,
  plan_id            plan_id     NOT NULL DEFAULT 'free' REFERENCES plans(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE subscriptions (
  user_id         text          PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  plan_id         plan_id       NOT NULL REFERENCES plans(id),
  cycle           billing_cycle NOT NULL DEFAULT 'monthly',
  payment_brand   text,
  payment_last4   text,
  payment_expiry  text,
  updated_at      timestamptz   NOT NULL DEFAULT now()
);
CREATE TRIGGER subscriptions_set_updated_at BEFORE UPDATE ON subscriptions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE invoices (
  id        text          PRIMARY KEY,
  user_id   text          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  issued_on date          NOT NULL,
  amount    numeric(10,2) NOT NULL,
  status    invoice_status NOT NULL
);
CREATE INDEX invoices_user_idx ON invoices (user_id, issued_on DESC);

-- ---------------------------------------------------------------------------
-- Data catalog (ingestion / datasets domain) — METADATA only, not contents
-- ---------------------------------------------------------------------------
CREATE TABLE datasets (
  id           text           PRIMARY KEY,
  owner_id     text           NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         text           NOT NULL,
  file_type    file_type      NOT NULL,
  meta         text,
  favorite     boolean        NOT NULL DEFAULT false,
  recent       boolean        NOT NULL DEFAULT false,
  status       dataset_status NOT NULL DEFAULT 'processing',
  row_count    integer,
  page_count   integer,
  column_count integer,
  size_label   text,
  error        text,
  storage_key  text,
  created_at   timestamptz    NOT NULL DEFAULT now(),
  updated_at   timestamptz    NOT NULL DEFAULT now()
);
CREATE INDEX datasets_owner_idx ON datasets (owner_id);
CREATE TRIGGER datasets_set_updated_at BEFORE UPDATE ON datasets
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE dataset_schemas (
  dataset_id text                 PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
  row_count  integer              NOT NULL DEFAULT 0,
  confidence inference_confidence NOT NULL DEFAULT 'media',
  confirmed  boolean              NOT NULL DEFAULT false,
  created_at timestamptz          NOT NULL DEFAULT now()
);

CREATE TABLE dataset_columns (
  id            bigint               GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_id    text                 NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  ordinal       integer              NOT NULL,
  name          text                 NOT NULL,
  type          column_type          NOT NULL,
  sample_values text,
  included      boolean              NOT NULL DEFAULT true,
  confidence    inference_confidence NOT NULL DEFAULT 'media',
  UNIQUE (dataset_id, ordinal)
);

-- ---------------------------------------------------------------------------
-- Chat (conversations + polymorphic messages)
-- ---------------------------------------------------------------------------
CREATE TABLE conversations (
  id         text        PRIMARY KEY,
  owner_id   text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dataset_id text        NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  title      text        NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner_idx ON conversations (owner_id, created_at DESC);
CREATE INDEX conversations_dataset_idx ON conversations (dataset_id);

CREATE TABLE messages (
  id              text         PRIMARY KEY,
  conversation_id text         NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  position        integer      NOT NULL,
  role            message_role NOT NULL,
  created_at      timestamptz  NOT NULL DEFAULT now(),
  content         jsonb        NOT NULL,
  UNIQUE (conversation_id, position)
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, position);

-- ---------------------------------------------------------------------------
-- Saved artifacts (dashboards + library domains)
-- ---------------------------------------------------------------------------
CREATE TABLE saved_charts (
  id           text        PRIMARY KEY,
  owner_id     text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dataset_id   text        REFERENCES datasets(id) ON DELETE SET NULL,
  dataset_name text        NOT NULL,
  question     text        NOT NULL,
  chart_type   chart_type  NOT NULL,
  saved_at     timestamptz NOT NULL DEFAULT now(),
  result       jsonb       NOT NULL
);
CREATE INDEX saved_charts_owner_idx ON saved_charts (owner_id, saved_at DESC);
CREATE INDEX saved_charts_dataset_idx ON saved_charts (dataset_id);

CREATE TABLE saved_queries (
  id           text        PRIMARY KEY,
  owner_id     text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question     text        NOT NULL,
  dataset_id   text        REFERENCES datasets(id) ON DELETE SET NULL,
  dataset_name text        NOT NULL,
  chart_type   chart_type  NOT NULL,
  saved_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX saved_queries_owner_idx ON saved_queries (owner_id, saved_at DESC);

-- ---------------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------------
CREATE TABLE notifications (
  id           text             PRIMARY KEY,
  user_id      text             NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tone         notification_tone NOT NULL,
  title        text             NOT NULL,
  body         text             NOT NULL,
  action_label text,
  action_href  text,
  read         boolean          NOT NULL DEFAULT false,
  created_at   timestamptz      NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications (user_id, read, created_at DESC);

-- ---------------------------------------------------------------------------
-- Cost ledger (billing domain — every paid action logged for the receipt UI)
-- ---------------------------------------------------------------------------
CREATE TABLE cost_ledger (
  id            text          PRIMARY KEY,
  user_id       text          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  occurred_at   timestamptz   NOT NULL DEFAULT now(),
  action_type   ledger_action NOT NULL,
  dataset_id    text          REFERENCES datasets(id) ON DELETE SET NULL,
  dataset_name  text,
  rows_returned integer       NOT NULL DEFAULT 0,
  cost_usd      numeric(12,4) NOT NULL DEFAULT 0,
  cost_ars      numeric(14,2) NOT NULL DEFAULT 0
);
CREATE INDEX cost_ledger_user_idx ON cost_ledger (user_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Auth support (optional): drop these if the backend goes JWT-stateless.
-- ---------------------------------------------------------------------------
CREATE TABLE verification_codes (
  id         text        PRIMARY KEY,
  user_id    text        REFERENCES users(id) ON DELETE CASCADE,
  email      text        NOT NULL,
  purpose    text        NOT NULL CHECK (purpose IN ('email_verify', 'two_factor')),
  code       text        NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed   boolean     NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX verification_codes_lookup_idx ON verification_codes (email, purpose);

CREATE TABLE auth_sessions (
  token      text        PRIMARY KEY,
  user_id    text        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_sessions_user_idx ON auth_sessions (user_id);

-- ---------------------------------------------------------------------------
-- Seed: plans (reference/config — mirrors backend/app/fixtures/plans.json)
-- ---------------------------------------------------------------------------
INSERT INTO plans (id, name, price_monthly, price_annual, recommended, features, limits) VALUES
  ('free', 'Free', 0, 0, false,
   '["3 datasets", "30 consultas/mes", "1.000 filas/consulta"]'::jsonb,
   '{"datasets": 3, "queriesPerMonth": 30, "rowsPerQuery": 1000}'::jsonb),
  ('pro', 'Pro', 29, 23, true,
   '["Datasets ilimitados", "1.000 consultas/mes", "50.000 filas por consulta", "Export CSV · PNG · PDF"]'::jsonb,
   '{"datasets": null, "queriesPerMonth": 1000, "rowsPerQuery": 50000}'::jsonb),
  ('business', 'Business', 79, 63, false,
   '["Todo sin límite práctico", "Prioridad de procesamiento", "Soporte con SLA", "Archivos hasta 1 GB"]'::jsonb,
   '{"datasets": null, "queriesPerMonth": null, "rowsPerQuery": null}'::jsonb);
"""

_DOWNGRADE_SQL = """
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS verification_codes;
DROP TABLE IF EXISTS cost_ledger;
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS saved_queries;
DROP TABLE IF EXISTS saved_charts;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS dataset_columns;
DROP TABLE IF EXISTS dataset_schemas;
DROP TABLE IF EXISTS datasets;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS plans;

DROP FUNCTION IF EXISTS set_updated_at();

DROP TYPE IF EXISTS notification_tone;
DROP TYPE IF EXISTS ledger_action;
DROP TYPE IF EXISTS invoice_status;
DROP TYPE IF EXISTS billing_cycle;
DROP TYPE IF EXISTS plan_id;
DROP TYPE IF EXISTS message_role;
DROP TYPE IF EXISTS chart_type;
DROP TYPE IF EXISTS inference_confidence;
DROP TYPE IF EXISTS column_type;
DROP TYPE IF EXISTS dataset_status;
DROP TYPE IF EXISTS file_type;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
