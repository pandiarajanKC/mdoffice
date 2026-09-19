"""OAuth calendar connection state (master spec sections 6-7, 43).

Tokens are stored encrypted (app/utils/token_crypto.py) — this table is
the only place they ever touch the database, and they are never exposed
to templates or logs.
"""
from app.extensions import db
from app.models.mixins import TimestampMixin


class CalendarIntegration(TimestampMixin, db.Model):
    __tablename__ = "md_calendar_integration"

    id = db.Column(db.Integer, primary_key=True)
    owner_username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, default="google")

    access_token_encrypted = db.Column(db.Text, nullable=False)
    refresh_token_encrypted = db.Column(db.Text, nullable=False)
    token_expiry = db.Column(db.DateTime, nullable=True)
    scope = db.Column(db.String(500), nullable=True)

    google_account_email = db.Column(db.String(255), nullable=True)
    connected_by = db.Column(db.String(64), nullable=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<CalendarIntegration {self.provider} for {self.owner_username}>"


class CalendarSyncLog(db.Model):
    __tablename__ = "md_calendar_sync_log"

    id = db.Column(db.Integer, primary_key=True)
    integration_id = db.Column(db.Integer, db.ForeignKey("md_calendar_integration.id"), nullable=False, index=True)
    triggered_by = db.Column(db.String(64), nullable=False)
    synced_at = db.Column(db.DateTime, nullable=False, index=True)
    events_fetched = db.Column(db.Integer, nullable=False, default=0)
    events_created = db.Column(db.Integer, nullable=False, default=0)
    events_updated = db.Column(db.Integer, nullable=False, default=0)
    success = db.Column(db.Boolean, nullable=False, default=True)
    error_message = db.Column(db.Text, nullable=True)

    integration = db.relationship(
        "CalendarIntegration", backref=db.backref("sync_logs", cascade="all, delete-orphan")
    )
