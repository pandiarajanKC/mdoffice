"""Tiny single-use handoff for "convert this email into a Project / Task /
Action Tracker item / Meeting" (see calendar_bp's email_convert route).

The draft lives in the session just long enough to survive one redirect
to the target list page. That page pops it — discarding it either way, so
it can never leak into a later, unrelated page load — and only actually
uses it if the target matches what that page creates, then prefills and
auto-opens its existing "New" modal with it.
"""
from flask import session


def pop_email_draft(*expected_targets: str) -> dict | None:
    draft = session.pop("email_convert_draft", None)
    if draft and draft.get("target") in expected_targets:
        return draft
    return None
