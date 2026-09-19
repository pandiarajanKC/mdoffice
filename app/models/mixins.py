"""Shared column mixins for application tables (mdoffice database).

All new application tables are prefixed with ``md_`` so they can never
collide with the pre-existing tables already present in the ``mdoffice``
database (meetings, action_items, people, departments, ...), which belong
to the predecessor system and are migrated in a later phase.
"""
from datetime import datetime, timezone

from app.extensions import db


def utcnow() -> datetime:
    """Naive UTC timestamp — matches how SQL Server's DATETIME (and SQLite,
    in tests) round-trip values, so stored timestamps stay comparable
    without tzinfo mismatches.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
