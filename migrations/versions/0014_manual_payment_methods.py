"""Manual payment methods — PaymentMethod model + submission FK

Revision ID: 0014_manual_payment_methods
Revises: 0013_paymongo_automated_billing
Create Date: 2026-06-06 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '0014_manual_payment_methods'
down_revision = '0013_paymongo_automated_billing'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'payment_methods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(120), nullable=False, server_default=''),
        sa.Column('method_type', sa.String(30), nullable=False, server_default='ewallet'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('instructions', sa.Text(), nullable=True),
        sa.Column('qr_image', sa.String(255), nullable=True),
        sa.Column('account_name', sa.String(120), nullable=True),
        sa.Column('account_number', sa.String(120), nullable=True),
        sa.Column('mobile_number', sa.String(50), nullable=True),
        sa.Column('bank_name', sa.String(120), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payment_methods_tenant_active', 'payment_methods', ['tenant_id', 'is_active'], unique=False)
    op.create_index('ix_payment_methods_display_order', 'payment_methods', ['display_order'], unique=False)
    op.create_index('ix_payment_methods_tenant_id', 'payment_methods', ['tenant_id'], unique=False)

    # Migrate legacy payment_instructions → payment_methods
    op.execute("""
        INSERT INTO payment_methods (
            tenant_id, name, method_type, is_active, is_default, instructions,
            qr_image, account_name, account_number, bank_name, notes,
            display_order, created_at, updated_at
        )
        SELECT
            tenant_id,
            COALESCE(NULLIF(title, ''), method, 'Payment Method'),
            CASE
                WHEN LOWER(method) LIKE '%paymongo%' THEN 'paymongo'
                WHEN LOWER(method) LIKE '%bank%' OR bank_name != '' THEN 'bank'
                WHEN LOWER(method) LIKE '%gcash%' OR LOWER(method) LIKE '%maya%' THEN 'ewallet'
                ELSE 'ewallet'
            END,
            is_active,
            0,
            description,
            qr_image,
            account_name,
            account_number,
            bank_name,
            '',
            0,
            created_at,
            updated_at
        FROM payment_instructions
    """)

    with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_method_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_payment_submissions_payment_method_id',
            'payment_methods',
            ['payment_method_id'],
            ['id'],
        )
        batch_op.create_index('ix_payment_submissions_payment_method_id', ['payment_method_id'])


def downgrade():
    with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
        batch_op.drop_index('ix_payment_submissions_payment_method_id')
        batch_op.drop_constraint('fk_payment_submissions_payment_method_id', type_='foreignkey')
        batch_op.drop_column('payment_method_id')

    op.drop_index('ix_payment_methods_tenant_id', table_name='payment_methods')
    op.drop_index('ix_payment_methods_display_order', table_name='payment_methods')
    op.drop_index('ix_payment_methods_tenant_active', table_name='payment_methods')
    op.drop_table('payment_methods')
