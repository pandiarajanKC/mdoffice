"""Role-based access control decorators.

Every protected route must verify authorization on the backend — frontend
button visibility is never sufficient on its own.
"""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user

from app.models.app_user import UserRole


def roles_required(*roles: str | UserRole):
    """Restrict a view to one or more roles.

    Usage: ``@roles_required("MD", "EA")`` or ``@roles_required(UserRole.MD)``.
    """
    allowed = {r.value if isinstance(r, UserRole) else str(r) for r in roles}

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role.value not in allowed:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def role_required(role: str | UserRole):
    return roles_required(role)
