from flask import abort, current_app, render_template, request
from flask_login import login_required

from app.reports import reports_bp
from app.reports.excel_export import export_to_excel
from app.reports.registry import REPORTS
from app.utils.rbac import roles_required


@reports_bp.route("/")
@login_required
@roles_required("MD", "EA")
def index():
    return render_template("reports/index.html", reports=REPORTS)


@reports_bp.route("/<report_key>")
@login_required
@roles_required("MD", "EA")
def view(report_key):
    report = REPORTS.get(report_key)
    if report is None:
        abort(404)

    employee_repository = current_app.extensions["employee_repository"]
    summary, rows = report["compute"](employee_repository)

    if request.args.get("export") == "excel":
        return export_to_excel(title=report["title"], columns=report["columns"], rows=rows)

    return render_template(
        "reports/view.html",
        report_key=report_key,
        report=report,
        summary=summary,
        rows=rows,
        is_ageing=report.get("summary_is_buckets", False),
    )
