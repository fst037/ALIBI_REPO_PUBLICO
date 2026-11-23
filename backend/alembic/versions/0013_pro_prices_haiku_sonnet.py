"""Pro reference prices for Haiku Quick / Sonnet Deep: US$ 31 / 24,80 / 0,05

Revision ID: 0013_pro_prices_haiku_sonnet
Revises: 0012_answer_modes_by_plan
Create Date: 2026-10-12

Quick answers with Haiku, Deep with Sonnet; Pro at US$ 31. The price now covers the full cost of an answer, including the
GPU hours of the endpoint serving modules A and C, which US$ 26 did not. Only
the USD reference values change; pesos are still derived on every read
(`app/core/pricing.py`), so no peso amount is stored here.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_pro_prices_haiku_sonnet"
down_revision = "0012_answer_modes_by_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE plans SET price_monthly_usd = 31, price_annual_usd = 24.80, overage_price_usd = 0.05 "
        "WHERE id = 'pro'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE plans SET price_monthly_usd = 26, price_annual_usd = 20.80, overage_price_usd = 0.04 "
        "WHERE id = 'pro'"
    )
