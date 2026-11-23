"""business model: Free + Pro in ARS, overage on Pro, no Business plan

Revision ID: 0010_business_model
Revises: 0009_conversation_answer_mode
Create Date: 2026-10-07

Aligns the plan catalogue with the business model: two plans priced in ARS, VAT excluded, and pay-per-query beyond
Pro's quota instead of a third fixed plan.

  plans          + currency, + overage_price; Free/Pro re-seeded (ARS prices,
                 new limits keys maxFileMb/historyDays/exportFormats);
                 the `business` row is deleted
  subscriptions  + overage_enabled, + overage_cap_ars
  users/subs     any `business` row moves to `pro` first (the FK would refuse
                 the delete otherwise)

**Why the `business` label stays in the `plan_id` type.** Postgres cannot drop
an enum value; the alternative is a new type plus `ALTER COLUMN ... TYPE` on
`plans.id` and the two columns that reference it, which means dropping and
re-creating both foreign keys and the `users.plan_id` default — on a database
this migration cannot be rehearsed against (private RDS behind the Zscaler
proxy). The orphan label is unreachable anyway:
both `users.plan_id` and `subscriptions.plan_id` reference `plans(id)`, and
there is no `business` row left to reference. The Python `PlanId` no longer has
the member, so nothing can write it either.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_business_model"
down_revision = "0009_conversation_answer_mode"
branch_labels = None
depends_on = None


_SEED = """
UPDATE plans SET
  currency = 'ARS', price_monthly = 0, price_annual = 0, overage_price = NULL,
  recommended = false,
  features = '["3 datasets", "30 consultas por mes", "1.000 filas por consulta", "Archivos de hasta 5 MB", "Historial de 7 días", "Exportación a CSV"]'::jsonb,
  limits = '{"datasets": 3, "queriesPerMonth": 30, "rowsPerQuery": 1000, "maxFileMb": 5, "historyDays": 7, "exportFormats": ["csv"]}'::jsonb
WHERE id = 'free';

UPDATE plans SET
  currency = 'ARS', price_monthly = 39900, price_annual = 31900, overage_price = 40,
  recommended = true,
  features = '["Datasets ilimitados", "1.000 consultas por mes", "5.000 filas por consulta", "Archivos de hasta 100 MB", "Historial ilimitado", "Exportación a CSV, PNG y PDF", "Consultas adicionales a $ 40 c/u, con tope de gasto"]'::jsonb,
  limits = '{"datasets": null, "queriesPerMonth": 1000, "rowsPerQuery": 5000, "maxFileMb": 100, "historyDays": null, "exportFormats": ["csv", "png", "pdf"]}'::jsonb
WHERE id = 'pro';
"""


def upgrade() -> None:
    op.add_column("plans", sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"))
    op.add_column("plans", sa.Column("overage_price", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "subscriptions",
        sa.Column("overage_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("subscriptions", sa.Column("overage_cap_ars", sa.Numeric(12, 2), nullable=True))

    op.execute("UPDATE subscriptions SET plan_id = 'pro' WHERE plan_id = 'business'")
    op.execute("UPDATE users SET plan_id = 'pro' WHERE plan_id = 'business'")
    op.execute("DELETE FROM plans WHERE id = 'business'")
    op.execute(_SEED)


def downgrade() -> None:
    # Restores the 0001 seed (USD prices, three plans). Users moved off
    # `business` stay on `pro`: which ones they were is not recorded.
    op.execute(
        """
        INSERT INTO plans (id, name, price_monthly, price_annual, recommended, features, limits) VALUES
          ('business', 'Business', 79, 63, false,
           '["Todo sin límite práctico", "Prioridad de procesamiento", "Soporte con SLA", "Archivos hasta 1 GB"]'::jsonb,
           '{"datasets": null, "queriesPerMonth": null, "rowsPerQuery": null}'::jsonb)
        ON CONFLICT (id) DO NOTHING;
        UPDATE plans SET price_monthly = 0, price_annual = 0, recommended = false,
          features = '["3 datasets", "30 consultas/mes", "1.000 filas/consulta"]'::jsonb,
          limits = '{"datasets": 3, "queriesPerMonth": 30, "rowsPerQuery": 1000}'::jsonb
        WHERE id = 'free';
        UPDATE plans SET price_monthly = 29, price_annual = 23, recommended = true,
          features = '["Datasets ilimitados", "1.000 consultas/mes", "50.000 filas por consulta", "Export CSV · PNG · PDF"]'::jsonb,
          limits = '{"datasets": null, "queriesPerMonth": 1000, "rowsPerQuery": 50000}'::jsonb
        WHERE id = 'pro';
        """
    )
    op.drop_column("subscriptions", "overage_cap_ars")
    op.drop_column("subscriptions", "overage_enabled")
    op.drop_column("plans", "overage_price")
    op.drop_column("plans", "currency")
