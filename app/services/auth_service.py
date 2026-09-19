"""Authentication business logic — kept out of routes per the layered
architecture (Route -> Service -> Repository/DB).
"""
from __future__ import annotations

import logging

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.app_user import AppUser, UserRole
from app.repositories.employee_repository import EmployeeRepository

security_logger = logging.getLogger("security")

# Documented temporary password for an employee's very first login only.
# It is never stored anywhere in plain text; the first successful login
# immediately hashes it and forces a change before any other action.
DEFAULT_EMPLOYEE_PASSWORD = "Pass"

MIN_PASSWORD_LENGTH = 8


class AuthenticationError(Exception):
    """Raised for any login failure. Message is always safe to show to the user."""


class PasswordPolicyError(Exception):
    """Raised when a new password fails policy checks."""


def authenticate(
    username: str,
    password: str,
    employee_repository: EmployeeRepository,
    *,
    max_attempts: int,
    lockout_minutes: int,
    ip_address: str | None = None,
) -> AppUser:
    username = (username or "").strip()
    if not username or not password:
        raise AuthenticationError("Invalid username or password.")

    user = AppUser.query.filter_by(username=username).first()

    if user is None:
        return _provision_and_authenticate_employee(
            username, password, employee_repository, ip_address=ip_address
        )

    if not user.is_active:
        security_logger.info("LOGIN_BLOCKED_INACTIVE user=%s", username)
        raise AuthenticationError("This account is inactive. Contact the EA/IT admin.")

    if user.is_locked():
        security_logger.info("LOGIN_BLOCKED_LOCKED user=%s", username)
        raise AuthenticationError(
            "This account is temporarily locked due to repeated failed attempts. Try again later."
        )

    if not user.check_password(password):
        user.register_failed_login(max_attempts, lockout_minutes)
        db.session.commit()
        security_logger.info("LOGIN_FAILED user=%s attempts=%s", username, user.failed_login_attempts)
        raise AuthenticationError("Invalid username or password.")

    user.register_successful_login()
    ActivityLog.record(
        entity_type="AUTH",
        entity_id=str(user.id),
        action_type="LOGIN_SUCCESS",
        performed_by=username,
        ip_address=ip_address,
    )
    db.session.commit()
    security_logger.info("LOGIN_SUCCESS user=%s role=%s", username, user.role.value)
    return user


def _provision_and_authenticate_employee(
    username: str,
    password: str,
    employee_repository: EmployeeRepository,
    *,
    ip_address: str | None,
) -> AppUser:
    employee = employee_repository.get_by_code(username)

    if employee is None or password != DEFAULT_EMPLOYEE_PASSWORD:
        security_logger.info("LOGIN_FAILED_UNKNOWN_OR_BAD_DEFAULT user=%s", username)
        raise AuthenticationError("Invalid username or password.")

    user = AppUser(
        username=employee.employee_code,
        role=UserRole.EMPLOYEE,
        employee_code=employee.employee_code,
        display_name=employee.name,
        must_change_password=True,
    )
    user.set_password(DEFAULT_EMPLOYEE_PASSWORD)
    user.register_successful_login()
    db.session.add(user)
    db.session.flush()

    ActivityLog.record(
        entity_type="AUTH",
        entity_id=str(user.id),
        action_type="EMPLOYEE_ACCOUNT_PROVISIONED",
        performed_by=username,
        ip_address=ip_address,
    )
    ActivityLog.record(
        entity_type="AUTH",
        entity_id=str(user.id),
        action_type="LOGIN_SUCCESS",
        performed_by=username,
        ip_address=ip_address,
    )
    db.session.commit()
    security_logger.info("EMPLOYEE_PROVISIONED_AND_LOGGED_IN user=%s", username)
    return user


def verify_identity_for_reset(
    username: str,
    date_of_birth,
    employee_repository: EmployeeRepository,
    *,
    max_attempts: int,
    lockout_minutes: int,
    ip_address: str | None = None,
) -> AppUser:
    """Self-service "forgot password" — no email is configured in this
    app, so instead of an emailed reset link, identity is proven against
    something the HR system already has on file: employee code + date of
    birth. Only works for accounts tied to a real HR (or external-contact)
    record with a known date of birth — an MD/EA account, or an external
    contact who was never given one, has no such record to check against
    and must go through the EA's manual reset instead (see
    force_password_reset below).

    Reuses the exact same lockout counters as ordinary password login:
    guessing someone's date of birth to bypass their password is exactly
    the kind of attempt that protection exists for, and it would defeat
    the point to only rate-limit one of the two ways in.
    """
    username = (username or "").strip()
    generic_error = "We couldn't verify your identity with those details. Contact your EA/IT admin for help."

    user = AppUser.query.filter_by(username=username).first()
    if user is None or not user.is_active:
        # Same message either way — never reveal whether an account exists.
        raise AuthenticationError(generic_error)

    if user.is_locked():
        raise AuthenticationError(
            "This account is temporarily locked due to repeated attempts. Try again later."
        )

    employee = employee_repository.get_by_code(user.employee_code) if user.employee_code else None
    if employee is None or employee.date_of_birth is None or employee.date_of_birth != date_of_birth:
        user.register_failed_login(max_attempts, lockout_minutes)
        db.session.commit()
        security_logger.info("PASSWORD_RESET_VERIFY_FAILED user=%s", username)
        raise AuthenticationError(generic_error)

    user.failed_login_attempts = 0
    user.locked_until = None
    ActivityLog.record(
        entity_type="AUTH", entity_id=str(user.id), action_type="PASSWORD_RESET_IDENTITY_VERIFIED",
        performed_by=username, ip_address=ip_address,
    )
    db.session.commit()
    security_logger.info("PASSWORD_RESET_VERIFY_SUCCESS user=%s", username)
    return user


def reset_password_after_verification(user: AppUser, new_password: str, confirm_password: str) -> None:
    """Completes a forgot-password reset — no current password to check,
    since proving identity via verify_identity_for_reset is what replaces
    that check here.
    """
    if new_password != confirm_password:
        raise PasswordPolicyError("New password and confirmation do not match.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")

    user.set_password(new_password)
    user.must_change_password = False
    ActivityLog.record(entity_type="AUTH", entity_id=str(user.id), action_type="PASSWORD_RESET_COMPLETED", performed_by=user.username)
    db.session.commit()
    security_logger.info("PASSWORD_RESET_COMPLETED user=%s", user.username)


def force_password_reset(user: AppUser, *, performed_by: str) -> str:
    """EA-triggered fallback for anyone who can't use the self-service
    identity check above (an MD/EA account, or an external contact with
    no date of birth on file) — puts the account back to the exact same
    state as a brand-new employee's first login: a known temporary
    password, forced to be changed before anything else. Returns that
    temporary password so the caller can show/share it once.
    """
    user.set_password(DEFAULT_EMPLOYEE_PASSWORD)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    ActivityLog.record(
        entity_type="AUTH", entity_id=str(user.id), action_type="PASSWORD_RESET_BY_EA", performed_by=performed_by,
    )
    db.session.commit()
    security_logger.info("PASSWORD_RESET_BY_EA user=%s performed_by=%s", user.username, performed_by)
    return DEFAULT_EMPLOYEE_PASSWORD


def change_password(user: AppUser, current_password: str, new_password: str, confirm_password: str) -> None:
    if not user.check_password(current_password):
        raise PasswordPolicyError("Current password is incorrect.")
    if new_password != confirm_password:
        raise PasswordPolicyError("New password and confirmation do not match.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if new_password == current_password:
        raise PasswordPolicyError("New password must be different from the current password.")

    user.set_password(new_password)
    user.must_change_password = False
    ActivityLog.record(
        entity_type="AUTH",
        entity_id=str(user.id),
        action_type="PASSWORD_CHANGED",
        performed_by=user.username,
    )
    db.session.commit()
    security_logger.info("PASSWORD_CHANGED user=%s", user.username)
