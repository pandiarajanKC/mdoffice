"""Add Tag and TagAssignment tables for the Tag Master feature

Revision ID: 94d0462c72f3
Revises: 4b8e29462403
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '94d0462c72f3'
down_revision = '4b8e29462403'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('md_tag',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('color', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_md_tag_name'), 'md_tag', ['name'], unique=True)

    op.create_table('md_tag_assignment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tag_id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('entity_id', sa.String(length=50), nullable=False),
    sa.Column('tagged_by', sa.String(length=64), nullable=False),
    sa.Column('tagged_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tag_id'], ['md_tag.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tag_id', 'entity_type', 'entity_id', name='uq_tag_assignment')
    )
    op.create_index(op.f('ix_md_tag_assignment_tag_id'), 'md_tag_assignment', ['tag_id'], unique=False)
    op.create_index(op.f('ix_md_tag_assignment_entity_type'), 'md_tag_assignment', ['entity_type'], unique=False)
    op.create_index(op.f('ix_md_tag_assignment_entity_id'), 'md_tag_assignment', ['entity_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_md_tag_assignment_entity_id'), table_name='md_tag_assignment')
    op.drop_index(op.f('ix_md_tag_assignment_entity_type'), table_name='md_tag_assignment')
    op.drop_index(op.f('ix_md_tag_assignment_tag_id'), table_name='md_tag_assignment')
    op.drop_table('md_tag_assignment')
    op.drop_index(op.f('ix_md_tag_name'), table_name='md_tag')
    op.drop_table('md_tag')
