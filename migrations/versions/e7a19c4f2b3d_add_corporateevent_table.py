"""Add CorporateEvent table for the Corporate Calendar feature

Revision ID: e7a19c4f2b3d
Revises: 94d0462c72f3
Create Date: 2026-09-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a19c4f2b3d'
down_revision = '94d0462c72f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('md_corporate_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('start_datetime', sa.DateTime(), nullable=False),
    sa.Column('end_datetime', sa.DateTime(), nullable=False),
    sa.Column('location', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_md_corporate_event_start_datetime'), 'md_corporate_event', ['start_datetime'], unique=False)
    op.create_index(op.f('ix_md_corporate_event_created_by'), 'md_corporate_event', ['created_by'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_md_corporate_event_created_by'), table_name='md_corporate_event')
    op.drop_index(op.f('ix_md_corporate_event_start_datetime'), table_name='md_corporate_event')
    op.drop_table('md_corporate_event')
