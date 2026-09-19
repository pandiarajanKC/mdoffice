"""People EA needs to assign work to who aren't in the HR Employee Master —
consultants, trustees, external agents. Deliberately NOT written into
EmployeeList (that table belongs to another system, see
app/repositories/employee_repository.py); this is this app's own small
roster, merged into EmployeeRepository's results so the rest of the app
(task/project/meeting assignment, dashboards, notifications, login) never
has to know the difference between a real employee and an external one.
"""
from app.extensions import db
from app.models.mixins import TimestampMixin


class ExternalContact(TimestampMixin, db.Model):
    __tablename__ = "md_external_contact"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    organization = db.Column(db.String(200), nullable=True)
    role_title = db.Column(db.String(100), nullable=True)  # e.g. "Consultant", "Trustee", "External Agent"
    email = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.String(64), nullable=False)

    @property
    def code(self) -> str:
        """The identifier used everywhere else in the app in place of a
        real AssociateCode — also this person's login username, so it must
        never collide with one. Real codes seen in this deployment are
        plain numeric strings; this prefix guarantees it never will.
        """
        return f"EXT{self.id}"

    def __repr__(self) -> str:
        return f"<ExternalContact {self.code} {self.name!r}>"
