"""prices tied to the dollar, and the record of the user's acceptance of the terms

Revision ID: 0011_usd_reference_prices
Revises: 0010_business_model
Create Date: 2026-10-11

Two changes the thesis's business model and
legal viability chapters ask for:

  plans  price_monthly / price_annual / overage_price (fixed ARS) become
         price_monthly_usd / price_annual_usd / overage_price_usd — the USD
         reference values. The peso price is computed on every read at the
         official rate (`app/core/pricing.py`), so it follows the rate instead
         of losing value between price reviews. Pro: US$ 26 / US$ 20,80 a month,
         overage US$ 0,04 a query ($ 40.300 / $ 32.240 / $ 62 at $ 1.550).
         The overage price leaves the Pro feature list (it was a fixed "$ 40").
  users  + terms_version, + terms_accepted_at — which version of the Terms and
         Privacy Policy the account accepted on sign-up, and when.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_usd_reference_prices"
down_revision = "0010_business_model"
branch_labels = None
depends_on = None


_PRO_FEATURES = (
    '["Datasets ilimitados", "1.000 consultas por mes", "5.000 filas por consulta", '
    '"Archivos de hasta 100 MB", "Historial ilimitado", "Exportación a CSV, PNG y PDF", '
    '"Consultas adicionales con tope de gasto", "Soporte por correo"]'
)


def upgrade() -> None:
    op.add_column("plans", sa.Column("price_monthly_usd", sa.Numeric(10, 2), nullable=True))
    op.add_column("plans", sa.Column("price_annual_usd", sa.Numeric(10, 2), nullable=True))
    op.add_column("plans", sa.Column("overage_price_usd", sa.Numeric(10, 4), nullable=True))
    op.execute("UPDATE plans SET price_monthly_usd = 0, price_annual_usd = 0, overage_price_usd = NULL WHERE id = 'free'")
    op.execute(
        "UPDATE plans SET price_monthly_usd = 26, price_annual_usd = 20.80, overage_price_usd = 0.04, "
        f"features = '{_PRO_FEATURES}'::jsonb WHERE id = 'pro'"
    )
    op.alter_column("plans", "price_monthly_usd", nullable=False)
    op.alter_column("plans", "price_annual_usd", nullable=False)
    op.drop_column("plans", "overage_price")
    op.drop_column("plans", "price_annual")
    op.drop_column("plans", "price_monthly")

    op.add_column("users", sa.Column("terms_version", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version")

    # Back to the 0010 fixed ARS prices.
    op.add_column("plans", sa.Column("price_monthly", sa.Numeric(10, 2), nullable=True))
    op.add_column("plans", sa.Column("price_annual", sa.Numeric(10, 2), nullable=True))
    op.add_column("plans", sa.Column("overage_price", sa.Numeric(10, 2), nullable=True))
    op.execute("UPDATE plans SET price_monthly = 0, price_annual = 0, overage_price = NULL WHERE id = 'free'")
    op.execute(
        "UPDATE plans SET price_monthly = 39900, price_annual = 31900, overage_price = 40, "
        "features = '[\"Datasets ilimitados\", \"1.000 consultas por mes\", \"5.000 filas por consulta\", "
        "\"Archivos de hasta 100 MB\", \"Historial ilimitado\", \"Exportación a CSV, PNG y PDF\", "
        "\"Consultas adicionales a $ 40 c/u, con tope de gasto\"]'::jsonb WHERE id = 'pro'"
    )
    op.alter_column("plans", "price_monthly", nullable=False)
    op.alter_column("plans", "price_annual", nullable=False)
    op.drop_column("plans", "overage_price_usd")
    op.drop_column("plans", "price_annual_usd")
    op.drop_column("plans", "price_monthly_usd")
