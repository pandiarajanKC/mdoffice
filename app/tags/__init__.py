from flask import Blueprint

tags_bp = Blueprint("tags", __name__, url_prefix="/tags", template_folder="../templates/tags")

from app.tags import routes  # noqa: E402,F401
