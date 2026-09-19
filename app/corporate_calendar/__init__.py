from flask import Blueprint

corporate_calendar_bp = Blueprint(
    "corporate_calendar", __name__, url_prefix="/corporate-calendar",
    template_folder="../templates/corporate_calendar",
)

from app.corporate_calendar import routes  # noqa: E402,F401
