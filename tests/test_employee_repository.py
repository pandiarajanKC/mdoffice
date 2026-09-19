"""Tests for the external-contact merge behavior added to EmployeeRepository
(app/models/external_contact.py — consultants, trustees, external agents
who aren't in the HR Employee Master).

EmployeeRepository's HR side talks to a real, separate SQL Server over its
own engine — never exercised in this test suite (see FakeEmployeeRepository
for that, used everywhere else). What's tested here is exactly the part
that's safe to exercise without one: every code path that only touches
external-contact codes never reaches self._engine at all, so a real
EmployeeRepository can be constructed with a harmless, never-connected-to
SQLite URI and used directly. Anything that mixes real HR codes with
external ones (or calls search()/list_all(), which always query the HR
side too) is instead verified live against the real app in conversation,
consistent with how the rest of this file's live-only dependencies
(Gmail, Calendar, HR SQL Server) are handled throughout this codebase.
"""
from app.extensions import db
from app.models.external_contact import ExternalContact
from app.repositories.employee_repository import EmployeeRepository, _external_contact_id, _external_to_record


def _repo() -> EmployeeRepository:
    return EmployeeRepository(database_uri="sqlite:///:memory:")


def _contact(db, **overrides) -> ExternalContact:
    contact = ExternalContact(
        name=overrides.pop("name", "Priya Sharma"),
        organization=overrides.pop("organization", "Vendor Co"),
        role_title=overrides.pop("role_title", "Consultant"),
        email=overrides.pop("email", "priya@vendor.com"),
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )
    db.session.add(contact)
    db.session.commit()
    return contact


def test_external_contact_id_parses_valid_code():
    assert _external_contact_id("EXT7") == 7
    assert _external_contact_id("ext7") == 7  # case-insensitive
    assert _external_contact_id(" EXT7 ") == 7  # tolerates stray whitespace


def test_external_contact_id_returns_none_for_ordinary_codes():
    assert _external_contact_id("7106") is None
    assert _external_contact_id("") is None
    assert _external_contact_id(None) is None
    assert _external_contact_id("EXTRA") is None  # starts with EXT but isn't one


def test_external_to_record_maps_fields():
    contact = ExternalContact(id=7, name="Priya Sharma", organization="Vendor Co", role_title="Consultant", email="priya@vendor.com", created_by="admin1")
    record = _external_to_record(contact)
    assert record.employee_code == "EXT7"
    assert record.associate_id == "EXT7"
    assert record.name == "Priya Sharma"
    assert record.department == "Vendor Co"
    assert record.designation == "Consultant"
    assert record.official_email == "priya@vendor.com"
    assert record.is_external is True
    assert record.date_of_birth is None


def test_get_by_code_resolves_external_contact_without_touching_hr_engine(app, db):
    contact = _contact(db)
    repo = _repo()

    record = repo.get_by_code(contact.code)
    assert record is not None
    assert record.name == "Priya Sharma"
    assert record.is_external is True


def test_get_by_code_returns_none_for_unknown_external_id(app, db):
    repo = _repo()
    assert repo.get_by_code("EXT9999") is None


def test_get_by_code_ignores_inactive_external_contact(app, db):
    contact = _contact(db, is_active=False)
    repo = _repo()
    assert repo.get_by_code(contact.code) is None


def test_exists_true_for_active_external_contact(app, db):
    contact = _contact(db)
    repo = _repo()
    assert repo.exists(contact.code) is True


def test_get_by_codes_resolves_multiple_external_contacts(app, db):
    a = _contact(db, name="Priya Sharma")
    b = _contact(db, name="Ravi Kumar")
    repo = _repo()

    results = repo.get_by_codes([a.code, b.code])
    assert set(results.keys()) == {a.code, b.code}
    assert results[a.code].name == "Priya Sharma"
    assert results[b.code].name == "Ravi Kumar"


def test_get_by_codes_empty_list_returns_empty_dict(app, db):
    repo = _repo()
    assert repo.get_by_codes([]) == {}
