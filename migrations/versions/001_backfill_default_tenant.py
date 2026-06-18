"""
migrations/versions/001_backfill_default_tenant.py

Alembic migration to:
  1. Ensure 'default' tenant_slug is set on existing Profile rows that lack it
  2. Backfill tenant_slug='default' on orphaned Skills/Projects/Testimonials/Inquiries
  3. Ensure admin User row has tenant_slug='default'

Run:
  flask db upgrade
  -- or directly:
  flask db upgrade 001_backfill_default_tenant
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '001_backfill_default_tenant'
down_revision = '0007_add_manual_payment_workflow'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── 1. Ensure profile table has tenant_slug column ─────────────────────────
    # (Idempotent: skips if column already exists)
    try:
        op.add_column('profile', sa.Column(
            'tenant_slug', sa.String(120),
            nullable=True, server_default='default'
        ))
    except Exception:
        pass  # Column already exists

    # ── 2. Backfill NULL tenant_slug on profile ────────────────────────────────
    conn.execute(text(
        "UPDATE profile SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
    ))

    # ── 3. Ensure at least one profile row with tenant_slug='default' ─────────
    result = conn.execute(text(
        "SELECT COUNT(*) FROM profile WHERE tenant_slug = 'default'"
    ))
    count = result.scalar()
    if count == 0:
        conn.execute(text("""
            INSERT INTO profile (
                name, title, subtitle, bio, bio_short, location, email,
                phone, profile_image, resume_url, years_experience,
                clients_count, hero_tagline, availability_status, is_available,
                social_links, tenant_slug, plan, monthly_rate, internal_notes,
                meta_title, meta_description, og_image, updated_at
            ) VALUES (
                'Portfolio Owner', 'Full Stack Developer',
                'Building beautiful digital experiences',
                'Welcome to my portfolio.', '', 'Remote', 'hello@example.com',
                '', '', '', 5, 0, 'Crafting elegant web experiences.',
                'Available for new work', true,
                '{}', 'default', 'Basic', 0.0, '',
                '', '', '', NOW()
            )
        """))
        print("Inserted default tenant profile row")

    # ── 4. Backfill tenant_slug on related tables ──────────────────────────────
    for table in ['skills', 'projects', 'testimonials', 'inquiries', 'activity_log']:
        try:
            conn.execute(text(
                f"UPDATE {table} SET tenant_slug = 'default' "
                f"WHERE tenant_slug IS NULL OR tenant_slug = ''"
            ))
        except Exception as e:
            print(f"  Skipped {table}: {e}")

    # ── 5. Backfill admin user tenant_slug ────────────────────────────────────
    try:
        conn.execute(text(
            "UPDATE users SET tenant_slug = 'default' "
            "WHERE (tenant_slug IS NULL OR tenant_slug = '') "
            "AND is_superadmin = false"
        ))
    except Exception as e:
        print(f"  Skipped users backfill: {e}")

    print("Migration 001_backfill_default_tenant: complete")


def downgrade():
    # Intentionally a no-op: data backfill is non-destructive
    pass
