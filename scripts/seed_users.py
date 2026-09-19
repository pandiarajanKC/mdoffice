"""Development-only seed script for the initial MD and EA accounts.

Run with:  python scripts/seed_users.py

If SEED_MD_PASSWORD / SEED_EA_PASSWORD are not set in .env, secure random
passwords are generated and printed ONCE to the console — they are never
written to source code or committed anywhere.
"""
from __future__ import annotations

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.app_user import AppUser, UserRole


def _generate_password() -> str:
    return secrets.token_urlsafe(9)


def _upsert(username: str, role: UserRole, display_name: str, password: str | None) -> tuple[AppUser, str | None]:
    user = AppUser.query.filter_by(username=username).first()
    generated = None

    if user is None:
        generated = password or _generate_password()
        user = AppUser(username=username, role=role, display_name=display_name, must_change_password=True)
        user.set_password(generated)
        db.session.add(user)
        print(f"Created {role.value} account '{username}'.")
    else:
        print(f"Account '{username}' already exists — leaving password unchanged.")

    return user, generated


def main() -> None:
    app = create_app()
    with app.app_context():
        md_user, md_pwd = _upsert(
            os.environ.get("SEED_MD_USERNAME", "admin"),
            UserRole.MD,
            "Managing Director",
            os.environ.get("SEED_MD_PASSWORD") or None,
        )
        ea_user, ea_pwd = _upsert(
            os.environ.get("SEED_EA_USERNAME", "admin1"),
            UserRole.EA,
            "Executive Assistant",
            os.environ.get("SEED_EA_PASSWORD") or None,
        )

        db.session.commit()

        if md_pwd or ea_pwd:
            print("\n" + "=" * 60)
            print("SAVE THESE TEMPORARY CREDENTIALS NOW — shown only once.")
            print("Each account must change its password on first login.")
            print("=" * 60)
            if md_pwd:
                print(f"MD account : {md_user.username} / {md_pwd}")
            if ea_pwd:
                print(f"EA account : {ea_user.username} / {ea_pwd}")
            print("=" * 60)


if __name__ == "__main__":
    main()
