from flask import Blueprint

ai_tools_bp = Blueprint("ai_tools", __name__, url_prefix="/ai-tools", template_folder="../templates/ai_tools")

from app.ai_tools import routes  # noqa: E402,F401
