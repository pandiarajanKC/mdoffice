from app.repositories.employee_repository import EmployeeRecord


class FakeEmployeeRepository:
    """In-memory stand-in for EmployeeRepository so auth tests never touch MSSQL."""

    def __init__(self, records: list[EmployeeRecord] | None = None):
        self._by_code = {r.employee_code: r for r in (records or [])}

    def get_by_code(self, employee_code: str):
        return self._by_code.get(employee_code)

    def get_by_codes(self, employee_codes: list[str]) -> dict:
        return {code: self._by_code[code] for code in employee_codes if code in self._by_code}

    def exists(self, employee_code: str) -> bool:
        return employee_code in self._by_code


def make_employee(code="5001", name="Test Employee", department="IT", date_of_birth=None, is_external=False) -> EmployeeRecord:
    return EmployeeRecord(
        associate_id="1",
        employee_code=code,
        name=name,
        company_id="1",
        department=department,
        designation="Executive",
        date_of_birth=date_of_birth,
        date_of_joining=None,
        gender="M",
        location="Madurai",
        manager_id=None,
        official_email=f"{code}@example.com",
        personal_email=None,
        pay_group="Staff",
        is_external=is_external,
    )
