"""Authentication identity for all three roles: MD, EA and Employee.

Employees already exist in the HR master (CUSTOMER_MASTER.EmployeeList) —
this table does NOT duplicate that data. For role=EMPLOYEE, ``employee_code``
points at ``EmployeeList.AssociateCode`` and profile fields (name,
department, email, ...) are always resolved live through
``EmployeeRepository`` rather than copied here.
"""
from __future__ import annotations

import enum

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.mixins import TimestampMixin, utcnow


class UserRole(str, enum.Enum):
    MD = "MD"
    EA = "EA"
    EMPLOYEE = "EMPLOYEE"


class AppUser(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "md_app_user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    role = db.Column(db.Enum(UserRole, name="md_user_role"), nullable=False)

    # Populated only for role=EMPLOYEE; the source of truth for the
    # employee's profile stays EmployeeList, joined by this code.
    employee_code = db.Column(db.String(50), nullable=True, index=True)

    # Used for MD/EA accounts, which have no EmployeeList row of their own.
    display_name = db.Column(db.String(200), nullable=True)

    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def register_failed_login(self, max_attempts: int, lockout_minutes: int) -> None:
        from datetime import timedelta

        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = utcnow() + timedelta(minutes=lockout_minutes)

    def register_successful_login(self) -> None:
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login_at = utcnow()

    def get_id(self) -> str:  # flask-login
        return str(self.id)

    def __repr__(self) -> str:
        return f"<AppUser {self.username} ({self.role.value})>"
