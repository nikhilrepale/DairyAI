"""Encrypted bank accounts and export audit.

Revision ID: c6a93d8ae142
Revises: f884c2711ebd
"""
from alembic import op
import sqlalchemy as sa
revision='c6a93d8ae142'
down_revision='f884c2711ebd'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('dairies',sa.Column('bank_details_encrypted',sa.Text(),nullable=True))
    op.add_column('customers',sa.Column('bank_details_encrypted',sa.Text(),nullable=True))
    op.create_table('bank_payment_exports',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('dairy_id',sa.Integer(),sa.ForeignKey('dairies.id'),nullable=False),sa.Column('period_id',sa.Integer(),sa.ForeignKey('billing_periods.id'),nullable=False),sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),sa.Column('beneficiary_count',sa.Integer(),nullable=False),sa.Column('total_amount',sa.Numeric(14,2),nullable=False),sa.Column('content_sha256',sa.String(64),nullable=False))
    op.create_index('ix_bank_payment_exports_dairy_id','bank_payment_exports',['dairy_id'])
    op.create_index('ix_bank_payment_exports_period_id','bank_payment_exports',['period_id'])
def downgrade():
    op.drop_index('ix_bank_payment_exports_period_id',table_name='bank_payment_exports')
    op.drop_index('ix_bank_payment_exports_dairy_id',table_name='bank_payment_exports')
    op.drop_table('bank_payment_exports')
    op.drop_column('customers','bank_details_encrypted')
    op.drop_column('dairies','bank_details_encrypted')
