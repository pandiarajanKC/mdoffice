from datetime import date

import pytest

from app.models.app_user import AppUser, UserRole
from app.services import auth_service
from tests.fakes import FakeEmployeeRepository, make_employee


def _auth(username, password, repo, db):
    return auth_service.authenticate(
        username, password, repo, max_attempts=5, lockout_minutes=15
    )


def test_password_hash_roundtrip():
    user = AppUser(username="x", role=UserRole.MD)
    user.set_password("Sup3rSecret!")
    assert user.check_password("Sup3rSecret!")
    assert not user.check_password("wrong")


def test_authenticate_success(app, db):
    user = AppUser(username="admin", role=UserRole.MD)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    repo = FakeEmployeeRepository()
    result = _auth("admin", "CorrectHorse1", repo, db)
    assert result.username == "admin"
    assert result.last_login_at is not None


def test_authenticate_wrong_password_generic_error(app, db):
    user = AppUser(username="admin", role=UserRole.MD)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    repo = FakeEmployeeRepository()
    with pytest.raises(auth_service.AuthenticationError) as exc:
        _auth("admin", "WrongPassword", repo, db)
    assert "Invalid username or password" in str(exc.value)


def test_authenticate_locks_after_max_attempts(app, db):
    user = AppUser(username="admin", role=UserRole.MD)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    repo = FakeEmployeeRepository()
    for _ in range(5):
        with pytest.raises(auth_service.AuthenticationError):
            _auth("admin", "WrongPassword", repo, db)

    with pytest.raises(auth_service.AuthenticationError) as exc:
        _auth("admin", "CorrectHorse1", repo, db)
    assert "locked" in str(exc.value).lower()


def test_employee_first_login_provisions_account(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", name="Priya R")])

    user = _auth("5001", auth_service.DEFAULT_EMPLOYEE_PASSWORD, repo, db)

    assert user.role == UserRole.EMPLOYEE
    assert user.employee_code == "5001"
    assert user.must_change_password is True
    assert AppUser.query.filter_by(username="5001").count() == 1


def test_default_password_stops_working_once_changed(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001")])
    user = _auth("5001", auth_service.DEFAULT_EMPLOYEE_PASSWORD, repo, db)

    auth_service.change_password(
        user, auth_service.DEFAULT_EMPLOYEE_PASSWORD, "NewEmpPass123", "NewEmpPass123"
    )

    with pytest.raises(auth_service.AuthenticationError):
        _auth("5001", auth_service.DEFAULT_EMPLOYEE_PASSWORD, repo, db)


def test_unknown_employee_code_rejected(app, db):
    repo = FakeEmployeeRepository()
    with pytest.raises(auth_service.AuthenticationError):
        _auth("9999999", auth_service.DEFAULT_EMPLOYEE_PASSWORD, repo, db)


def test_inactive_account_rejected(app, db):
    user = AppUser(username="admin", role=UserRole.MD, is_active=False)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    repo = FakeEmployeeRepository()
    with pytest.raises(auth_service.AuthenticationError) as exc:
        _auth("admin", "CorrectHorse1", repo, db)
    assert "inactive" in str(exc.value).lower()


def test_change_password_requires_correct_current(app, db):
    user = AppUser(username="admin", role=UserRole.MD)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    with pytest.raises(auth_service.PasswordPolicyError):
        auth_service.change_password(user, "wrong-current", "NewPassword1", "NewPassword1")


def test_change_password_success_clears_must_change_flag(app, db):
    user = AppUser(username="admin", role=UserRole.MD, must_change_password=True)
    user.set_password("CorrectHorse1")
    db.session.add(user)
    db.session.commit()

    auth_service.change_password(user, "CorrectHorse1", "NewPassword1", "NewPassword1")
    assert user.must_change_password is False
    assert user.check_password("NewPassword1")


# ---------- self-service "forgot password" (identity check via DOB) ----------

def _provision_employee(repo, db, code="5001", dob=date(1990, 5, 17)):
    auth_service.authenticate(code, auth_service.DEFAULT_EMPLOYEE_PASSWORD, repo, max_attempts=5, lockout_minutes=15)
    return AppUser.query.filter_by(username=code).first()


def test_verify_identity_for_reset_success(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", date_of_birth=date(1990, 5, 17))])
    _provision_employee(repo, db)

    user = auth_service.verify_identity_for_reset(
        "5001", date(1990, 5, 17), repo, max_attempts=5, lockout_minutes=15
    )
    assert user.username == "5001"


def test_verify_identity_for_reset_wrong_dob_generic_error(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", date_of_birth=date(1990, 5, 17))])
    _provision_employee(repo, db)

    with pytest.raises(auth_service.AuthenticationError) as exc:
        auth_service.verify_identity_for_reset("5001", date(2000, 1, 1), repo, max_attempts=5, lockout_minutes=15)
    assert "couldn't verify" in str(exc.value).lower()


def test_verify_identity_for_reset_unknown_username_same_generic_error(app, db):
    repo = FakeEmployeeRepository()
    with pytest.raises(auth_service.AuthenticationError) as exc:
        auth_service.verify_identity_for_reset("nobody", date(1990, 1, 1), repo, max_attempts=5, lockout_minutes=15)
    assert "couldn't verify" in str(exc.value).lower()


def test_verify_identity_for_reset_fails_when_no_dob_on_file(app, db):
    # MD/EA accounts and external contacts have no HR date of birth to
    # check against — must fail closed, not treat "unknown" as a match.
    user = AppUser(username="admin1", role=UserRole.EA, display_name="EA")
    user.set_password("whatever")
    db.session.add(user)
    db.session.commit()

    repo = FakeEmployeeRepository()
    with pytest.raises(auth_service.AuthenticationError):
        auth_service.verify_identity_for_reset("admin1", date(1990, 1, 1), repo, max_attempts=5, lockout_minutes=15)


def test_verify_identity_for_reset_locks_after_max_attempts(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", date_of_birth=date(1990, 5, 17))])
    _provision_employee(repo, db)

    for _ in range(5):
        with pytest.raises(auth_service.AuthenticationError):
            auth_service.verify_identity_for_reset("5001", date(2000, 1, 1), repo, max_attempts=5, lockout_minutes=15)

    with pytest.raises(auth_service.AuthenticationError) as exc:
        auth_service.verify_identity_for_reset("5001", date(1990, 5, 17), repo, max_attempts=5, lockout_minutes=15)
    assert "locked" in str(exc.value).lower()


def test_reset_password_after_verification_success(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", date_of_birth=date(1990, 5, 17))])
    user = _provision_employee(repo, db)

    auth_service.reset_password_after_verification(user, "BrandNewPass1", "BrandNewPass1")
    assert user.check_password("BrandNewPass1")
    assert user.must_change_password is False


def test_reset_password_after_verification_mismatch_rejected(app, db):
    repo = FakeEmployeeRepository([make_employee(code="5001", date_of_birth=date(1990, 5, 17))])
    user = _provision_employee(repo, db)

    with pytest.raises(auth_service.PasswordPolicyError):
        auth_service.reset_password_after_verification(user, "BrandNewPass1", "Different1")


# ---------- EA-triggered password reset fallback ----------

def test_force_password_reset_sets_temp_password_and_must_change(app, db):
    user = AppUser(username="5001", role=UserRole.EMPLOYEE, employee_code="5001")
    user.set_password("WhateverTheyHad1")
    user.failed_login_attempts = 3
    db.session.add(user)
    db.session.commit()

    temp_password = auth_service.force_password_reset(user, performed_by="admin1")

    assert temp_password == auth_service.DEFAULT_EMPLOYEE_PASSWORD
    assert user.check_password(auth_service.DEFAULT_EMPLOYEE_PASSWORD)
    assert user.must_change_password is True
    assert user.failed_login_attempts == 0
