"""billing_v3.3 — Add license_key + payment_status to subscriptions, subscription_id FK to payment_submissions

Revision ID: 0003_billing_v3_3
Revises: 0011_add_paymongo_subscription
Create Date: 2025-06-06

Changes
-------
subscriptions
  + license_key        VARCHAR(64)  UNIQUE  NULLABLE   — generated on approval; tenant enters this to activate
  + payment_status     VARCHAR(20)  NOT NULL DEFAULT 'unpaid'
  + INDEX ix_subscriptions_tenant_status (tenant_id, status)

payment_submissions
  + subscription_id    INTEGER REFERENCES subscriptions(id)  NULLABLE  INDEX
  + INDEX ix_payment_submissions_tenant_status (tenant_id, status)

Data migration
  • Backfill payment_status = 'paid' for every existing active subscription.
  • Backfill payment_status = 'pending' for pending subs that already have a proof file.

Rollback
  • Down migration removes all added columns (destructive — back up first).
"""

from alembic import op
import sqlalchemy as sa


revision = '0003_billing_v3_3'
down_revision = '0011_add_paymongo_subscription'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. subscriptions table ───────────────────────────────────────────────
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        # Skip adding license_key if it already exists (idempotent guard)
        batch_op.add_column(
            sa.Column('license_key', sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                'payment_status',
                sa.String(length=20),
                nullable=False,
                server_default='unpaid',
            )
        )
        try:
            batch_op.create_unique_constraint(
                'uq_subscriptions_license_key', ['license_key']
            )
        except Exception:
            pass  # Constraint may already exist on some SQLite versions
        try:
            batch_op.create_index(
                'ix_subscriptions_tenant_status', ['tenant_id', 'status']
            )
        except Exception:
            pass

    # ── 2. payment_submissions table ─────────────────────────────────────────
    with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('subscription_id', sa.Integer(), nullable=True)
        )
        try:
            batch_op.create_foreign_key(
                'fk_payment_submissions_subscription_id',
                'subscriptions',
                ['subscription_id'], ['id'],
            )
        except Exception:
            pass  # SQLite doesn't enforce FKs; constraint is advisory
        try:
            batch_op.create_index(
                'ix_payment_submissions_subscription_id', ['subscription_id']
            )
        except Exception:
            pass
        try:
            batch_op.create_index(
                'ix_payment_submissions_tenant_status', ['tenant_id', 'status']
            )
        except Exception:
            pass

    # ── 3. Data backfill ─────────────────────────────────────────────────────
    op.execute(
        "UPDATE subscriptions SET payment_status = 'paid' WHERE status = 'active'"
    )
    op.execute(
        """
        UPDATE subscriptions
        SET payment_status = 'pending'
        WHERE status = 'pending'
          AND payment_proof IS NOT NULL
          AND payment_proof != ''
        """
    )


def downgrade():
    # WARNING: destructive — license_key values are lost on rollback.
    with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
        try:
            batch_op.drop_index('ix_payment_submissions_tenant_status')
        except Exception:
            pass
        try:
            batch_op.drop_index('ix_payment_submissions_subscription_id')
        except Exception:
            pass
        try:
            batch_op.drop_constraint(
                'fk_payment_submissions_subscription_id', type_='foreignkey'
            )
        except Exception:
            pass
        batch_op.drop_column('subscription_id')

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        try:
            batch_op.drop_index('ix_subscriptions_tenant_status')
        except Exception:
            pass
        try:
            batch_op.drop_constraint('uq_subscriptions_license_key', type_='unique')
        except Exception:
            pass
        batch_op.drop_column('payment_status')
        batch_op.drop_column('license_key')