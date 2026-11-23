"""Commercial policy: Pro US$ 20 / 16 with 250 queries, overage US$ 0,08, Free with 20

Revision ID: 0016_pro_prices_and_quotas
Revises: 0015_dataset_cleaning_recipe
Create Date: 2026-10-18

Commercial policy. Prices go back to the
reference of the business model in the thesis: Pro costs US$ 20 a month, or
US$ 16 a month paid yearly; it includes 250 queries a month and each extra one
is US$ 0,08. Free drops from 30 to 20 queries a month. Only the USD reference
values and the quotas change; pesos are still derived on every read
(`app/core/pricing.py`), so no peso amount is stored here.

The feature lists carry the quota as text, so they are rewritten whole, in the
order of `app/fixtures/plans.json`.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0016_pro_prices_and_quotas"
down_revision = "0015_dataset_cleaning_recipe"
branch_labels = None
depends_on = None


def _features(free_queries: str, pro_queries: str) -> str:
    return f"""
        UPDATE plans SET
          features = '["3 datasets", "{free_queries} consultas por mes", "1.000 filas por consulta", "Archivos de hasta 5 MB", "Historial de 7 días", "Exportación a CSV", "Respuestas en modo Rápido"]'::jsonb
        WHERE id = 'free';
        UPDATE plans SET
          features = '["Datasets ilimitados", "{pro_queries} consultas por mes", "5.000 filas por consulta", "Archivos de hasta 100 MB", "Historial ilimitado", "Exportación a CSV, PNG y PDF", "Respuestas en modo Rápido y Profundo", "Consultas adicionales con tope de gasto", "Soporte por correo"]'::jsonb
        WHERE id = 'pro';
        """


def upgrade() -> None:
    op.execute(
        "UPDATE plans SET price_monthly_usd = 20, price_annual_usd = 16, overage_price_usd = 0.08 "
        "WHERE id = 'pro'"
    )
    op.execute(
        """
        UPDATE plans SET limits = jsonb_set(limits, '{queriesPerMonth}', '20') WHERE id = 'free';
        UPDATE plans SET limits = jsonb_set(limits, '{queriesPerMonth}', '250') WHERE id = 'pro';
        """
    )
    op.execute(_features("20", "250"))


def downgrade() -> None:
    op.execute(
        "UPDATE plans SET price_monthly_usd = 31, price_annual_usd = 24.80, overage_price_usd = 0.05 "
        "WHERE id = 'pro'"
    )
    op.execute(
        """
        UPDATE plans SET limits = jsonb_set(limits, '{queriesPerMonth}', '30') WHERE id = 'free';
        UPDATE plans SET limits = jsonb_set(limits, '{queriesPerMonth}', '1000') WHERE id = 'pro';
        """
    )
    op.execute(_features("30", "1.000"))
