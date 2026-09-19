from app.utils.email_draft import pop_email_draft


def test_pop_email_draft_returns_matching_draft(app):
    with app.test_request_context():
        from flask import session
        session["email_convert_draft"] = {"target": "project", "title": "T", "description": "D"}
        draft = pop_email_draft("project")
        assert draft == {"target": "project", "title": "T", "description": "D"}
        assert "email_convert_draft" not in session  # single-use


def test_pop_email_draft_discards_mismatched_target(app):
    with app.test_request_context():
        from flask import session
        session["email_convert_draft"] = {"target": "meeting", "title": "T", "description": "D"}
        assert pop_email_draft("project") is None
        assert "email_convert_draft" not in session  # still popped, not leaked to a later page


def test_pop_email_draft_returns_none_when_absent(app):
    with app.test_request_context():
        assert pop_email_draft("project", "task") is None
