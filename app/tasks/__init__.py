from flask import Blueprint

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

from app.tasks import routes  # noqa: E402,F401
