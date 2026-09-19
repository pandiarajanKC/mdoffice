"""OAuth email connection state — an EA's own Gmail inbox, connected the
same way as the MD's calendar (see app/models/calendar_integration.py),
so they can search their mail and turn a message into a Project, Task,
Action Tracker item, or Meeting without leaving this app.

Tokens are stored encrypted (app/utils/token_crypto.py) — this table is
the only place they ever touch the database, and they are never exposed
to templates or logs. No email content is stored here or anywhere else —
searches and message bodies are fetched live from Gmail each time.
"""
from app.extensions import db
from app.models.mixins import TimestampMixin


class EmailIntegration(TimestampMixin, db.Model):
    __tablename__ = "md_email_integration"

    id = db.Column(db.Integer, primary_key=True)
    owner_username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, default="google")

    access_token_encrypted = db.Column(db.Text, nullable=False)
    refresh_token_encrypted = db.Column(db.Text, nullable=False)
    token_expiry = db.Column(db.DateTime, nullable=True)
    scope = db.Column(db.String(500), nullable=True)

    google_account_email = db.Column(db.String(255), nullable=True)
    connected_by = db.Column(db.String(64), nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<EmailIntegration {self.provider} for {self.owner_username}>"
