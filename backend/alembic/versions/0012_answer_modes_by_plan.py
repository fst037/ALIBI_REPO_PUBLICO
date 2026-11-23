"""answer depth by plan: Free answers Quick, Pro may ask Deep

Revision ID: 0012_answer_modes_by_plan
Revises: 0011_usd_reference_prices
Create Date: 2026-10-11

Deep is a Pro feature. Quick/Deep
(0009_conversation_answer_mode) stays; which depths a plan may ask for becomes a
plan limit, `limits.answerModes`, enforced in `app/services/billing.py`, and
each plan's feature list says so.

Conversations already saved as `deep` by a Free user are left alone: their runs
are answered Quick (`services/runs.start_run`), so a later upgrade restores
them without anyone having to set the mode again.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_answer_modes_by_plan"
down_revision = "0011_usd_reference_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE plans SET
          limits = limits || '{"answerModes": ["quick"]}'::jsonb,
          features = features || '["Respuestas en modo Rápido"]'::jsonb
        WHERE id = 'free';
        UPDATE plans SET
          limits = limits || '{"answerModes": ["quick", "deep"]}'::jsonb,
          features = '["Datasets ilimitados", "1.000 consultas por mes", "5.000 filas por consulta", "Archivos de hasta 100 MB", "Historial ilimitado", "Exportación a CSV, PNG y PDF", "Respuestas en modo Rápido y Profundo", "Consultas adicionales con tope de gasto", "Soporte por correo"]'::jsonb
        WHERE id = 'pro';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE plans SET limits = limits - 'answerModes',
          features = features - 'Respuestas en modo Rápido'
        WHERE id = 'free';
        UPDATE plans SET limits = limits - 'answerModes',
          features = features - 'Respuestas en modo Rápido y Profundo'
        WHERE id = 'pro';
        """
    )
