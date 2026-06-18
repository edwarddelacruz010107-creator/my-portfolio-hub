"""Trial enforcement v3.3 — no schema change, data integrity backfill

Revision ID: 0012_trial_enforcement_v3
Revises: 0011_add_paymongo_subscription
Create Date: 2026-06-06 00:00:00.000000

Summary
-------
All new logic (is_expired, enforce_expiry) is computed from existing columns:
  profile.free_trial_ends   (DateTime, already exists)
  profile.free_trial_days   (Integer, already exists)
  subscription.status       (String, already exists)
  subscription.expires_at   (DateTime, already exists)

No DDL changes needed.

This migration only performs a DATA BACKFILL:
  1. Sets tenant.status = 'suspended' for any tenant whose trial has ended
     and who has no active subscription. Safe to run repeatedly (idempotent).
  2. Sets profile.free_trial_ends = profile.created_at + free_trial_days for
     any profile with free_trial_days > 0 but NULL free_trial_ends (fixes
     legacy rows created before v3.3).

Run: flask db upgrade
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone, timedelta


revision = '0012_trial_enforcement_v3'
down_revision = '0011_add_paymongo_subscription'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── 1. Backfill free_trial_ends for profiles with free_trial_days but no end date ──
    rows = conn.execute(sa.text(
        """
        SELECT p.id, p.tenant_id, p.free_trial_days, t.created_at
        FROM profile p
        JOIN tenants t ON t.id = p.tenant_id
        WHERE p.free_trial_days > 0
          AND p.free_trial_ends IS NULL
        """
    )).fetchall()

    for row in rows:
        profile_id, tenant_id, trial_days, created_at = row

        # Normalise created_at to aware datetime
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        if hasattr(created_at, 'tzinfo') and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        trial_ends = created_at + timedelta(days=int(trial_days))

        conn.execute(sa.text(
            "UPDATE profile SET free_trial_ends = :te WHERE id = :pid"
        ), {'te': trial_ends, 'pid': profile_id})

    # ── 2. Suspend tenants whose trial has ended with no active subscription ──
    now = datetime.now(timezone.utc)

    expired_tenant_ids = conn.execute(sa.text(
        """
        SELECT p.tenant_id
        FROM profile p
        WHERE p.free_trial_ends IS NOT NULL
          AND p.free_trial_ends <= :now
          AND p.tenant_id NOT IN (
              SELECT s.tenant_id FROM subscriptions s
              WHERE s.status = 'active'
                AND (s.expires_at IS NULL OR s.expires_at > :now)
          )
        """
    ), {'now': now}).fetchall()

    for (tenant_id,) in expired_tenant_ids:
        conn.execute(sa.text(
            "UPDATE tenants SET status = 'suspended' WHERE id = :tid AND status != 'suspended'"
        ), {'tid': tenant_id})

    # ── 3. Mark expired active subscriptions as expired ──────────────────────
    conn.execute(sa.text(
        """
        UPDATE subscriptions
        SET status = 'expired'
        WHERE status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at <= :now
        """
    ), {'now': now})


def downgrade():
    # Data backfill is not reversible; DDL is a no-op so downgrade is safe
    pass
