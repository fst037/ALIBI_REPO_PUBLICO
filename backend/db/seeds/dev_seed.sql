-- ALIBI — development seed data (NOT a migration)
--
-- Loads the same sample data the frontend saw from the JSON fixtures
-- (backend/app/fixtures/datasets.json + schemas.json) into a real database, so
-- the DB-backed `datasets` endpoints return exactly what the mock ones did.
-- Run it once against your dev database AFTER the schema migrations:
--
--   psql "host=localhost port=5433 dbname=alibi user=alibi_admin sslmode=require" \
--     -v ON_ERROR_STOP=1 -f backend/db/seeds/dev_seed.sql
--
-- Requires migration 0004_english_enum_values to have been applied: the
-- `column_type`, `inference_confidence` and `chart_type` values below are the
-- English labels ('Text', 'high', 'bar'), which only exist after that revision.
-- Against an un-migrated database every INSERT here fails on the enum cast.
--
-- Re-runnable: it deletes the seeded user first (cascading to their datasets /
-- schemas / columns) and re-inserts. It only touches the 'u_ana' demo user's
-- data — nothing else in the database. This is dev-only sample data; never run
-- it against anything holding real data.

BEGIN;

-- Deleting the demo user cascades to their datasets, schemas and columns.
DELETE FROM users WHERE id = 'u_ana';

-- Demo user (owner of the sample datasets). plan_id defaults to 'free'.
-- password_hash is bcrypt for the dev password "demo1234" — log in with
-- ana@acme.com / demo1234. email_verified=true so nothing gates the login.
INSERT INTO users (id, email, name, workspace, password_hash, email_verified) VALUES
  ('u_ana', 'ana@acme.com', 'Ana Ruiz', 'Acme',
   '$2b$12$Yn3e2r7.G/MotREY/H2RyOD21ndmthAXzzysV6yHIqbHCmJUmdGvS', true);

-- Datasets (mirror datasets.json). owner_id = the demo user. updated_at is a
-- real timestamp; the API formats it back to 'YYYY-MM-DD'.
--
-- The id is also the SQL table name Module A generates against (see
-- app/services/text2sql/ddl.py::table_name), which is why it is a plain English
-- identifier rather than a random string here.
INSERT INTO datasets
  (id, owner_id, name, file_type, meta, favorite, recent, status,
   row_count, page_count, column_count, size_label, updated_at) VALUES
  ('sales',     'u_ana', 'sales_2024.csv',       'csv',  '12,480 rows · 3 days ago',  true,  true,  'processed', 12480, NULL, 6,    '2.1 MB', '2026-07-08'),
  ('budget',    'u_ana', 'budget_q1.xlsx',       'xlsx', '840 rows · 5 days ago',     false, true,  'processed', 840,   NULL, 4,    '512 KB', '2026-07-06'),
  ('customers', 'u_ana', 'active_customers.csv', 'csv',  '5,210 rows · 1 week ago',   true,  true,  'processed', 5210,  NULL, 5,    '1.3 MB', '2026-07-04'),
  ('inventory', 'u_ana', 'warehouse_stock.xlsx', 'xlsx', '3,100 rows · 2 weeks ago',  false, false, 'processed', 3100,  NULL, 5,    '720 KB', '2026-06-27'),
  ('report',    'u_ana', 'annual_report.pdf',    'pdf',  '18 pages · 1 month ago',    false, false, 'processed', NULL,  18,   NULL, '4.8 MB', '2026-06-11');

-- Inferred schemas (mirror schemas.json). Only sales / customers / budget have
-- one; inventory and report don't — so GET /datasets/{id}/schema returns 409 for
-- those, matching the fixture behaviour. confirmed=true since they're processed.
INSERT INTO dataset_schemas (dataset_id, row_count, confidence, confirmed) VALUES
  ('sales',     12480, 'high',   true),
  ('customers', 5210,  'high',   true),
  ('budget',    840,   'medium', true);

-- Columns per schema (mirror schemas.json), ordered by `ordinal`.
INSERT INTO dataset_columns
  (dataset_id, ordinal, name, type, sample_values, included, confidence) VALUES
  ('sales',     0, 'date',        'Date',         '2024-01-05, 2024-01-06…',  true, 'high'),
  ('sales',     1, 'region',      'Text',         'Buenos Aires, Córdoba…',   true, 'high'),
  ('sales',     2, 'product',     'Text',         'Notebook, Monitor…',       true, 'high'),
  ('sales',     3, 'sales',       'Number',       '128450, 84210…',           true, 'high'),
  ('sales',     4, 'units',       'Whole number', '42, 18, 63…',              true, 'high'),
  ('sales',     5, 'margin_pct',  'Percentage',   '12.4, 6.1, 8.3…',          true, 'medium'),
  ('customers', 0, 'customer_id', 'Whole number', '1001, 1002…',              true, 'high'),
  ('customers', 1, 'name',        'Text',         'Acme SA, Globex…',         true, 'high'),
  ('customers', 2, 'segment',     'Text',         'Retail, Wholesale…',       true, 'high'),
  ('customers', 3, 'signup_date', 'Date',         '2023-03-11, 2023-05-02…',  true, 'high'),
  ('customers', 4, 'active',      'Boolean',      'true, false…',             true, 'medium'),
  ('budget',    0, 'area',        'Text',         'Marketing, Sales…',        true, 'high'),
  ('budget',    1, 'month',       'Date',         '2024-01, 2024-02…',        true, 'medium'),
  ('budget',    2, 'budget',      'Number',       '500000, 320000…',          true, 'high'),
  ('budget',    3, 'spent',       'Number',       '480200, 291000…',          true, 'medium');

-- Saved queries (mirror savedQueries.json), owned by the demo user.
INSERT INTO saved_queries (id, owner_id, question, dataset_id, dataset_name, chart_type, saved_at) VALUES
  ('sq_sales_region',      'u_ana', 'Top 5 regions by total sales in 2024', 'sales',     'sales_2024.csv',       'bar',  '2026-07-08'),
  ('sq_monthly_sales',     'u_ana', 'Monthly sales trend',                  'sales',     'sales_2024.csv',       'line', '2026-07-07'),
  ('sq_margin',            'u_ana', 'Margin by product',                    'sales',     'sales_2024.csv',       'bar',  '2026-07-05'),
  ('sq_customers_segment', 'u_ana', 'Customer distribution by segment',     'customers', 'active_customers.csv', 'pie',  '2026-07-02');

-- Notifications (mirror notifications.json). created_at is relative to now() so
-- the API's "time" label renders live ("12 min ago", "yesterday", …).
-- Apostrophes are doubled per SQL string-literal escaping.
INSERT INTO notifications (id, user_id, tone, title, body, action_label, action_href, read, created_at) VALUES
  ('n_1', 'u_ana', 'ok',   'sales_2024.csv está listo',            'Procesamos tu archivo (12.480 filas). Ya podés empezar a hacer preguntas.', 'Preguntar ahora',        '/app',         false, now() - interval '12 minutes'),
  ('n_2', 'u_ana', 'warn', 'Cerca de tu límite de consultas',     'Usaste 27 de las 30 consultas de tu plan Free este mes.',                  'Ver planes',      '/suscripcion', false, now() - interval '2 hours'),
  ('n_3', 'u_ana', 'warn', 'Llegaste al límite de datasets',     'Tenés 3 de 3 datasets. Eliminá uno o mejorá tu plan para agregar más.',    'Administrar biblioteca', '/biblioteca',  true,  now() - interval '1 day'),
  ('n_4', 'u_ana', 'info', 'Novedad: mapa de calor',              'Agregamos el mapa de calor a los tipos de visualización disponibles.',         NULL,             NULL,           true,  now() - interval '3 days'),
  ('n_5', 'u_ana', 'err',  'No pudimos procesar report.pdf',     'El archivo no tenía tablas reconocibles. Probá con un archivo CSV o Excel.', 'Reintentar subida',   '/ingesta',     true,  now() - interval '4 days');

-- Subscription (mirror subscription.json: Free, no payment method). Usage is
-- derived at read time, not stored here.
INSERT INTO subscriptions (user_id, plan_id, cycle) VALUES ('u_ana', 'free', 'monthly');

-- Cost ledger (a couple of recent entries so derived usage is non-zero).
INSERT INTO cost_ledger (id, user_id, occurred_at, action_type, dataset_id, dataset_name, rows_returned, cost_usd, cost_ars) VALUES
  ('cl_1', 'u_ana', now() - interval '2 days', 'query',         'sales', 'sales_2024.csv', 5, 0.012, 15.6),
  ('cl_2', 'u_ana', now() - interval '3 days', 'visualization', 'sales', 'sales_2024.csv', 6, 0.008, 10.4);

COMMIT;
