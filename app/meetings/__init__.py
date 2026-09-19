from flask import Blueprint

meetings_bp = Blueprint("meetings", __name__, url_prefix="/meetings", template_folder="../templates/meetings")

from app.meetings import routes  # noqa: E402,F401
from app.meetings import ai_routes  # noqa: E402,F401
