"""Background scheduler for reminders and escalations (master spec section
38): "Initial implementation may use APScheduler carefully, but design
for future worker/service deployment."

Kept deliberately thin — each job just calls into the same service
functions a route or test could call directly, so moving this to a
dedicated worker process later (Celery, a Windows Scheduled Task, etc.)
means relocating this file, not rewriting business logic.
"""
from __future__ import annotations

import os

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def init_scheduler(app) -> None:
    global _scheduler
    if _scheduler is not None:
        return  # already running in this process

    if not app.config.get("ENABLE_SCHEDULER", True):
        return

    # Flask's debug reloader runs a monitor process and a worker process;
    # only the worker (WERKZEUG_RUN_MAIN=true) should actually run jobs,
    # or the monitor process would start a second, duplicate scheduler.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    scheduler = BackgroundScheduler(daemon=True)

    def _fire_reminders_job():
        with app.app_context():
            from app.services import reminder_service

            try:
                reminder_service.fire_due_reminders()
            except Exception:  # noqa: BLE001 - a job failure must never kill the scheduler thread
                app.logger.exception("fire_due_reminders job failed")

    def _escalations_job():
        with app.app_context():
            from app.services import escalation_service

            try:
                escalation_service.run_escalations()
            except Exception:  # noqa: BLE001
                app.logger.exception("run_escalations job failed")

    scheduler.add_job(_fire_reminders_job, "interval", minutes=5, id="fire_due_reminders", replace_existing=True)
    scheduler.add_job(_escalations_job, "interval", minutes=60, id="run_escalations", replace_existing=True)
    scheduler.start()

    _scheduler = scheduler
    app.logger.info("Background scheduler started: reminders every 5m, escalations every 60m.")
