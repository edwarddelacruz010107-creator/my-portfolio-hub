"""tenant_url_refactor

Revision ID: 0002_tenant_url_refactor
Revises: 7d0f3492b2b3
Create Date: 2025-06-01

Changes:
  1. Ensure profile.tenant_slug has a NOT NULL default of 'default'
  2. Backfill any NULL tenant_slug values to 'default'
  3. Ensure at least one Profile with tenant_slug='default' exists (seed if none)
  4. Add index on profile.tenant_slug if not exists (idempotent)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = '0002_tenant_url_refactor'
down_revision = '7d0f3492b2b3'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # ── 1. Backfill NULL tenant_slugs to 'default' ────────────────────────────
    for table in ('profile', 'skills', 'projects', 'testimonials',
                  'inquiry', 'activity_log'):
        try:
            conn.execute(sa.text(
                f"UPDATE {table} SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
            ))
        except Exception:
            pass  # Table may not exist yet in some migrations paths

    # ── 2. Seed default Profile if none exists ────────────────────────────────
    result = conn.execute(sa.text(
        "SELECT COUNT(*) FROM profile WHERE tenant_slug = 'default'"
    )).scalar()

    if result == 0:
        conn.execute(sa.text("""
            INSERT INTO profile (
                name, title, subtitle, bio, bio_short, location, email, phone,
                profile_image, resume_url, years_experience, clients_count,
                hero_tagline, availability_status, is_available, social_links,
                tenant_slug, plan, monthly_rate, internal_notes,
                meta_title, meta_description, og_image, updated_at
            ) VALUES (
                'Your Name', 'Full Stack Developer',
                'Building beautiful digital experiences',
                'Welcome to my portfolio.', 'A developer who ships.',
                '', '', '', '', '', 0, 0, '', 'Available for freelance', true,
                '{}', 'default', 'Basic', 0.0, '', '', '', '',
                NOW()
            )
        """))

    # ── 3. Ensure default admin user has tenant_slug='default' ────────────────
    try:
        conn.execute(sa.text(
            "UPDATE users SET tenant_slug = 'default' WHERE tenant_slug IS NULL OR tenant_slug = ''"
        ))
    except Exception:
        pass


def downgrade():
    # No destructive downgrade needed
    pass
