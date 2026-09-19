from flask import Blueprint

employees_bp = Blueprint("employees", __name__, url_prefix="/employees")

from app.employees import routes  # noqa: E402,F401
