from datetime import date

from googleapiclient.errors import HttpError

from app.services.email.gmail_service import GmailService, _friendly_error


class _FakeHttpResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"


def _api_not_enabled_error():
    body = (
        b'{"error": {"errors": [{"reason": "accessNotConfigured"}], '
        b'"message": "Gmail API has not been used in project 123456 before or it is disabled."}}'
    )
    return HttpError(_FakeHttpResponse(403), body)


def test_build_query_combines_all_filters():
    query = GmailService._build_query(
        from_query="alice@example.com", subject_query="Budget Review",
        date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
    )
    assert query == 'from:alice@example.com subject:"Budget Review" after:2026/01/01 before:2026/02/01'


def test_build_query_omits_missing_filters():
    query = GmailService._build_query(from_query="alice@example.com", subject_query=None, date_from=None, date_to=None)
    assert query == "from:alice@example.com"


def test_build_query_empty_when_nothing_provided():
    query = GmailService._build_query(from_query=None, subject_query=None, date_from=None, date_to=None)
    assert query == ""


def test_extract_body_prefers_plain_text_over_nested_html():
    service = GmailService(client_id="id", client_secret="secret", redirect_uri="https://example.com/callback")
    # "Hello there" base64url-encoded (Gmail's own encoding for message bodies)
    plain_data = "SGVsbG8gdGhlcmU"
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": "PGgxPkhlbGxvPC9oMT4"}},
            {"mimeType": "text/plain", "body": {"data": plain_data}},
        ],
    }
    assert service._extract_body(payload) == "Hello there"


def test_extract_body_returns_none_when_only_html_present():
    service = GmailService(client_id="id", client_secret="secret", redirect_uri="https://example.com/callback")
    payload = {"mimeType": "text/html", "body": {"data": "PGgxPkhlbGxvPC9oMT4"}}
    assert service._extract_body(payload) is None


def test_friendly_error_explains_gmail_api_not_enabled():
    message = _friendly_error(_api_not_enabled_error(), "Could not search Gmail")
    assert "Gmail API isn't turned on yet" in message
    assert "Enable" in message


def test_friendly_error_falls_back_to_raw_message_for_other_errors():
    message = _friendly_error(ValueError("network timeout"), "Could not search Gmail")
    assert message == "Could not search Gmail: network timeout"
