"""Expand CorporateEvent to the full Corporate Calendar Event Submission
Form (Sections 2-5) and switch start/end to date-only, since the source
form has no time-of-day fields.

Revision ID: b3f9d61a8c2e
Revises: e7a19c4f2b3d
Create Date: 2026-09-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f9d61a8c2e'
down_revision = 'e7a19c4f2b3d'
branch_labels = None
depends_on = None


def upgrade():
    # ---- Section 2: Event Details ----
    op.add_column('md_corporate_event', sa.Column(
        'event_type',
        sa.Enum('CONFERENCE_TRADE_SHOW', 'BOARD_MANAGEMENT_MEETING', 'REGULATORY_AUDIT', 'VIP_GUEST_VISIT',
                'TRAINING', 'PRODUCT_LAUNCH', 'STATUTORY_DEADLINE', 'OTHER', name='md_corp_event_type'),
        nullable=False, server_default='OTHER',
    ))
    op.add_column('md_corporate_event', sa.Column('event_type_other', sa.String(length=200), nullable=True))
    op.add_column('md_corporate_event', sa.Column('organizer', sa.String(length=200), nullable=True))

    # Date-only replacements for the old start_datetime/end_datetime — the
    # source form has no time-of-day fields. Added nullable first so the
    # existing row can be backfilled below, then locked to NOT NULL.
    op.add_column('md_corporate_event', sa.Column('start_date', sa.Date(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('end_date', sa.Date(), nullable=True))
    op.execute("UPDATE md_corporate_event SET start_date = CAST(start_datetime AS DATE), end_date = CAST(end_datetime AS DATE)")
    op.alter_column('md_corporate_event', 'start_date', existing_type=sa.Date(), nullable=False)
    op.alter_column('md_corporate_event', 'end_date', existing_type=sa.Date(), nullable=False)

    op.drop_index(op.f('ix_md_corporate_event_start_datetime'), table_name='md_corporate_event')
    op.drop_column('md_corporate_event', 'start_datetime')
    op.drop_column('md_corporate_event', 'end_datetime')
    op.create_index(op.f('ix_md_corporate_event_start_date'), 'md_corporate_event', ['start_date'], unique=False)

    # ---- Section 3: Priority & Impact Assessment ----
    op.add_column('md_corporate_event', sa.Column(
        'priority_level', sa.Enum('CRITICAL', 'HIGH', 'STANDARD', name='md_corp_priority_level'),
        nullable=False, server_default='STANDARD',
    ))
    op.add_column('md_corporate_event', sa.Column(
        'md_presence_required', sa.Enum('YES', 'NO', 'AWARENESS_ONLY', name='md_corp_md_involvement'),
        nullable=False, server_default='NO',
    ))
    op.add_column('md_corporate_event', sa.Column('involves_external_stakeholder', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('md_corporate_event', sa.Column('has_critical_impact', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('md_corporate_event', sa.Column('key_participants', sa.Text(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('estimated_attendees', sa.Integer(), nullable=True))

    # ---- Section 4: Preparation & Action Required ----
    op.add_column('md_corporate_event', sa.Column('pre_event_actions', sa.Text(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('documents_to_prepare', sa.Text(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('budget_involved', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('md_corporate_event', sa.Column('budget_amount', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('md_corporate_event', sa.Column('preparation_deadline', sa.Date(), nullable=True))
    op.add_column('md_corporate_event', sa.Column(
        'conflicts_with_events', sa.Enum('YES', 'NO', 'NOT_SURE', name='md_corp_conflict_status'), nullable=True,
    ))
    op.add_column('md_corporate_event', sa.Column('conflict_notes', sa.Text(), nullable=True))

    # ---- Section 5: Attachments ----
    op.add_column('md_corporate_event', sa.Column('attachment_types', sa.Text(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('attachment_notes', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('md_corporate_event', 'attachment_notes')
    op.drop_column('md_corporate_event', 'attachment_types')

    op.drop_column('md_corporate_event', 'conflict_notes')
    op.drop_column('md_corporate_event', 'conflicts_with_events')
    op.drop_column('md_corporate_event', 'preparation_deadline')
    op.drop_column('md_corporate_event', 'budget_amount')
    op.drop_column('md_corporate_event', 'budget_involved')
    op.drop_column('md_corporate_event', 'documents_to_prepare')
    op.drop_column('md_corporate_event', 'pre_event_actions')

    op.drop_column('md_corporate_event', 'estimated_attendees')
    op.drop_column('md_corporate_event', 'key_participants')
    op.drop_column('md_corporate_event', 'has_critical_impact')
    op.drop_column('md_corporate_event', 'involves_external_stakeholder')
    op.drop_column('md_corporate_event', 'md_presence_required')
    op.drop_column('md_corporate_event', 'priority_level')

    op.add_column('md_corporate_event', sa.Column('start_datetime', sa.DateTime(), nullable=True))
    op.add_column('md_corporate_event', sa.Column('end_datetime', sa.DateTime(), nullable=True))
    op.execute("UPDATE md_corporate_event SET start_datetime = CAST(start_date AS DATETIME), end_datetime = CAST(end_date AS DATETIME)")
    op.alter_column('md_corporate_event', 'start_datetime', existing_type=sa.DateTime(), nullable=False)
    op.alter_column('md_corporate_event', 'end_datetime', existing_type=sa.DateTime(), nullable=False)
    op.drop_index(op.f('ix_md_corporate_event_start_date'), table_name='md_corporate_event')
    op.drop_column('md_corporate_event', 'start_date')
    op.drop_column('md_corporate_event', 'end_date')
    op.create_index(op.f('ix_md_corporate_event_start_datetime'), 'md_corporate_event', ['start_datetime'], unique=False)

    op.drop_column('md_corporate_event', 'organizer')
    op.drop_column('md_corporate_event', 'event_type_other')
    op.drop_column('md_corporate_event', 'event_type')
