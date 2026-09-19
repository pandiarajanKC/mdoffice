from __future__ import annotations

from datetime import datetime

from flask import Flask, current_app, redirect, render_template, request, url_for
from flask_login import current_user

from app.config import get_config
from app.extensions import csrf, db, login_manager, migrate
from app.repositories.employee_repository import EmployeeRepository
from app.utils.logging_setup import configure_logging


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or get_config())

    if app.debug:
        # Catch missing template variables loudly during development instead
        # of Jinja's default of silently treating them as falsy — this class
        # of bug (a route forgetting to pass a variable a template checks
        # with {% if %}) would otherwise render a subtly wrong page with no
        # error at all.
        from jinja2 import StrictUndefined

        app.jinja_env.undefined = StrictUndefined

    configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    app.extensions["employee_repository"] = EmployeeRepository(
        app.config["EMPLOYEE_DATABASE_URI"],
        table_name=app.config["DB_EMPLOYEE_TABLE"],
    )

    _register_blueprints(app)
    _register_login_manager(app)
    _register_error_handlers(app)
    _register_context_processors(app)
    _register_hooks(app)
    _register_template_helpers(app)

    return app


def _register_blueprints(app: Flask) -> None:
    from app.ai_tools import ai_tools_bp
    from app.auth import auth_bp
    from app.calendar import calendar_bp
    from app.corporate_calendar import corporate_calendar_bp
    from app.dashboard import dashboard_bp
    from app.employees import employees_bp
    from app.meetings import meetings_bp
    from app.notifications import notifications_bp
    from app.projects import projects_bp
    from app.reports import reports_bp
    from app.tags import tags_bp
    from app.tasks import tasks_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(meetings_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(corporate_calendar_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(ai_tools_bp)
    app.register_blueprint(tags_bp)


def _register_login_manager(app: Flask) -> None:
    from app.models.app_user import AppUser

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(AppUser, int(user_id))


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        current_app.logger.error("Unhandled server error: %s", e)
        return render_template("errors/500.html"), 500


def _register_context_processors(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        display_name = ""
        notification_unread_count = 0
        recent_notifications = []
        if current_user.is_authenticated:
            if current_user.role.value == "EMPLOYEE":
                repo = current_app.extensions["employee_repository"]
                profile = repo.get_by_code(current_user.employee_code)
                display_name = profile.name if profile else current_user.username
            else:
                display_name = current_user.display_name or current_user.username

            from app.notifications.routes import resolve_notification_url
            from app.services import notification_service

            notification_unread_count = notification_service.unread_count(current_user.username)
            recent_notifications = [
                {"notification": n, "url": resolve_notification_url(n)}
                for n in notification_service.recent_for(current_user.username, limit=6)
            ]

        hour = datetime.now().hour
        day_period = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"

        return {
            "product_name": app.config["PRODUCT_NAME"],
            "product_subtitle": app.config["PRODUCT_SUBTITLE"],
            "current_year": datetime.now().year,
            "current_user_display_name": display_name,
            "today_str": datetime.now().strftime("%A, %d %B %Y"),
            "day_period": day_period,
            "notification_unread_count": notification_unread_count,
            "recent_notifications": recent_notifications,
        }


def _register_template_helpers(app: Flask) -> None:
    from app.utils.badges import MEETING_STATUS_BADGE, PRIORITY_BADGE, TASK_STATUS_BADGE, badge_class
    from app.utils.task_display import task_source_label

    app.jinja_env.globals["badge_class"] = badge_class
    app.jinja_env.globals["MEETING_STATUS_BADGE"] = MEETING_STATUS_BADGE
    app.jinja_env.globals["TASK_STATUS_BADGE"] = TASK_STATUS_BADGE
    app.jinja_env.globals["PRIORITY_BADGE"] = PRIORITY_BADGE
    app.jinja_env.globals["task_source_label"] = task_source_label


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def enforce_password_change():
        if not current_user.is_authenticated:
            return None
        if not getattr(current_user, "must_change_password", False):
            return None

        allowed_endpoints = {"auth.change_password", "auth.logout", "static"}
        if request.endpoint in allowed_endpoints:
            return None
        return redirect(url_for("auth.change_password"))
