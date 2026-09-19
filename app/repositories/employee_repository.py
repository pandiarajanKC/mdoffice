"""Read-only access to the existing HR Employee Master.

Source: CUSTOMER_MASTER.dbo.EmployeeList (owned by another system — this
application never writes to it, and never copies its data into its own
tables). Column mapping below was captured by inspecting the live schema
(see ``docs`` note in README) rather than assumed:

    AssociateId    -> internal numeric id (not used as login)
    AssociateCode  -> employee_code (unique; this is the login username)
    AssociateName  -> name
    CompanyId      -> company_id
    Department     -> department
    Designation    -> designation
    Dob            -> date_of_birth
    DOJ            -> date_of_joining
    Gender         -> gender
    Location       -> location
    ManagerId      -> manager_id (references another row's AssociateId, NOT
                       AssociateCode — verified against live data: ~98% of
                       ManagerId values match an AssociateId, only ~7% happen
                       to also match an AssociateCode by coincidence)
    OffMailId      -> official_email (blank for ~57% of employees)
    PayGroupName   -> pay_group (Staff / NAPS / NATS)
    PerMailId      -> personal_email
    Status         -> NOT used: every row has an empty string here, so it
                       cannot signal active/inactive. Account activation is
                       tracked in AppUser.is_active instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import create_engine, or_, text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class EmployeeRecord:
    associate_id: str
    employee_code: str
    name: str
    company_id: str | None
    department: str | None
    designation: str | None
    date_of_birth: date | None
    date_of_joining: date | None
    gender: str | None
    location: str | None
    manager_id: str | None
    official_email: str | None
    personal_email: str | None
    pay_group: str | None
    # True for a consultant/trustee/external agent (app/models/external_contact.py)
    # rather than a real row from the HR Employee Master. Defaulted so every
    # existing caller that builds an EmployeeRecord is unaffected.
    is_external: bool = False

    @property
    def email(self) -> str | None:
        """Best available email for notifications: official, else personal."""
        return self.official_email or self.personal_email or None


_COLUMNS = (
    "AssociateId, AssociateCode, AssociateName, CompanyId, Department, "
    "Designation, Dob, DOJ, Gender, Location, ManagerId, OffMailId, "
    "PayGroupName, PerMailId"
)


def _row_to_record(row) -> EmployeeRecord:
    return EmployeeRecord(
        associate_id=str(row.AssociateId).strip(),
        employee_code=str(row.AssociateCode).strip(),
        name=(row.AssociateName or "").strip(),
        company_id=(row.CompanyId or "").strip() or None,
        department=(row.Department or "").strip() or None,
        designation=(row.Designation or "").strip() or None,
        date_of_birth=row.Dob.date() if row.Dob else None,
        date_of_joining=row.DOJ.date() if row.DOJ else None,
        gender=(row.Gender or "").strip() or None,
        location=(row.Location or "").strip() or None,
        manager_id=(row.ManagerId or "").strip() or None,
        official_email=(row.OffMailId or "").strip() or None,
        personal_email=(row.PerMailId or "").strip() or None,
        pay_group=(row.PayGroupName or "").strip() or None,
    )


EXTERNAL_CODE_PREFIX = "EXT"


def _external_to_record(contact) -> EmployeeRecord:
    return EmployeeRecord(
        associate_id=contact.code,
        employee_code=contact.code,
        name=contact.name,
        company_id=None,
        department=contact.organization,
        designation=contact.role_title,
        date_of_birth=None,
        date_of_joining=contact.created_at.date() if contact.created_at else None,
        gender=None,
        location=None,
        manager_id=None,
        official_email=contact.email,
        personal_email=None,
        pay_group=None,
        is_external=True,
    )


def _external_contact_id(code: str) -> int | None:
    """The numeric id inside an external contact's code (see
    ExternalContact.code), or None if this doesn't look like one at all —
    lets every lookup below skip a wasted query for an ordinary employee code.
    """
    code = (code or "").strip().upper()
    if not code.startswith(EXTERNAL_CODE_PREFIX):
        return None
    try:
        return int(code[len(EXTERNAL_CODE_PREFIX):])
    except ValueError:
        return None


class EmployeeRepository:
    """Thin, read-only repository over EmployeeList — plus this app's own
    small external-contacts roster (consultants, trustees, external agents;
    see app/models/external_contact.py) merged in transparently, so every
    caller throughout this app (task/project/meeting assignment, dashboards,
    notifications, login) works with either kind of person identically
    without needing to know which one it's looking at.

    Uses its own SQLAlchemy engine for the HR side (independent of the
    app's ORM session) because that table lives in a different database and
    is never written to or migrated by this application. External contacts,
    in contrast, live in this app's own database and are queried through
    its normal ORM models.
    """

    def __init__(self, database_uri: str, table_name: str = "EmployeeList") -> None:
        self._table_name = table_name
        self._engine: Engine = create_engine(database_uri, pool_pre_ping=True, pool_recycle=280)

    def get_by_code(self, employee_code: str) -> EmployeeRecord | None:
        ext_id = _external_contact_id(employee_code)
        if ext_id is not None:
            return self._get_external_by_id(ext_id)

        sql = text(
            f"SELECT {_COLUMNS} FROM {self._table_name} WHERE AssociateCode = :code"
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"code": employee_code}).fetchone()
        return _row_to_record(row) if row else None

    def _get_external_by_id(self, ext_id: int) -> EmployeeRecord | None:
        from app.models.external_contact import ExternalContact

        contact = ExternalContact.query.filter_by(id=ext_id, is_active=True).first()
        return _external_to_record(contact) if contact else None

    def exists(self, employee_code: str) -> bool:
        return self.get_by_code(employee_code) is not None

    def get_by_codes(self, employee_codes: list[str]) -> dict[str, EmployeeRecord]:
        """Batch lookup — avoids N+1 queries when resolving assignee names for a list of tasks."""
        from app.models.external_contact import ExternalContact

        codes = list(dict.fromkeys(c for c in employee_codes if c))
        if not codes:
            return {}

        ext_ids: set[int] = set()
        real_codes = []
        for code in codes:
            ext_id = _external_contact_id(code)
            if ext_id is not None:
                ext_ids.add(ext_id)
            else:
                real_codes.append(code)

        results: dict[str, EmployeeRecord] = {}
        if ext_ids:
            contacts = ExternalContact.query.filter(
                ExternalContact.id.in_(ext_ids), ExternalContact.is_active == True  # noqa: E712 - .is_(True) compiles to invalid "IS 1" syntax on MSSQL
            ).all()
            for contact in contacts:
                results[contact.code] = _external_to_record(contact)

        if real_codes:
            placeholders = ", ".join(f":code{i}" for i in range(len(real_codes)))
            sql = text(f"SELECT {_COLUMNS} FROM {self._table_name} WHERE AssociateCode IN ({placeholders})")
            params = {f"code{i}": code for i, code in enumerate(real_codes)}
            with self._engine.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            for row in rows:
                record = _row_to_record(row)
                results[record.employee_code] = record

        return results

    def get_by_ids(self, associate_ids: list[str]) -> dict[str, EmployeeRecord]:
        """Batch lookup by AssociateId — needed for ManagerId, which references
        AssociateId rather than AssociateCode (see module docstring)."""
        ids = list(dict.fromkeys(i for i in associate_ids if i))
        if not ids:
            return {}
        placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
        sql = text(f"SELECT {_COLUMNS} FROM {self._table_name} WHERE AssociateId IN ({placeholders})")
        params = {f"id{i}": associate_id for i, associate_id in enumerate(ids)}
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r.associate_id: r for r in (_row_to_record(row) for row in rows)}

    def search(self, query: str, limit: int = 25) -> list[EmployeeRecord]:
        sql = text(
            f"""
            SELECT TOP (:limit) {_COLUMNS} FROM {self._table_name}
            WHERE AssociateCode LIKE :pattern OR AssociateName LIKE :pattern
            ORDER BY AssociateName
            """
        )
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"pattern": pattern, "limit": limit}).fetchall()
        results = [_row_to_record(r) for r in rows]
        results.extend(self._search_external(query, limit=max(0, limit - len(results))))
        return results

    def _search_external(self, query: str, limit: int) -> list[EmployeeRecord]:
        from app.models.external_contact import ExternalContact

        if limit <= 0:
            return []
        pattern = f"%{query}%"
        contacts = (
            ExternalContact.query.filter(
                ExternalContact.is_active == True,  # noqa: E712 - .is_(True) compiles to invalid "IS 1" syntax on MSSQL
                or_(ExternalContact.name.ilike(pattern), ExternalContact.organization.ilike(pattern)),
            )
            .order_by(ExternalContact.name)
            .limit(limit)
            .all()
        )
        return [_external_to_record(c) for c in contacts]

    def list_by_department(self, department: str) -> list[EmployeeRecord]:
        sql = text(
            f"SELECT {_COLUMNS} FROM {self._table_name} WHERE Department = :dept ORDER BY AssociateName"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"dept": department}).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_all(self, limit: int = 500) -> list[EmployeeRecord]:
        from app.models.external_contact import ExternalContact

        sql = text(f"SELECT TOP (:limit) {_COLUMNS} FROM {self._table_name} ORDER BY AssociateName")
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"limit": limit}).fetchall()
        results = [_row_to_record(r) for r in rows]

        contacts = ExternalContact.query.filter_by(is_active=True).order_by(ExternalContact.name).all()
        results.extend(_external_to_record(c) for c in contacts)
        return results

    def list_departments(self) -> list[str]:
        sql = text(
            f"SELECT DISTINCT Department FROM {self._table_name} "
            f"WHERE Department IS NOT NULL AND Department <> '' ORDER BY Department"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [r[0] for r in rows]
