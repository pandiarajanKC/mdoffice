"""Gmail implementation of BaseEmailService.

Requires GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
GOOGLE_OAUTH_REDIRECT_URI (the same OAuth client already used for Calendar
sync — one Google Cloud OAuth client can request different scopes across
different flows) plus the Gmail API enabled on that Cloud project.
"""
from __future__ import annotations

import base64
import os
from datetime import date, timedelta
from email.utils import parseaddr, parsedate_to_datetime

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google sometimes grants a superset of requested scopes

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.services.email.base_email_service import (
    BaseEmailService,
    EmailAuthError,
    EmailFetchError,
    EmailMessage,
    EmailTokenSet,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "openid", "https://www.googleapis.com/auth/userinfo.email"]

_API_NOT_ENABLED_MESSAGE = (
    "The Gmail API isn't turned on yet for this app's Google Cloud project — Calendar Sync and "
    "Gmail search are enabled separately, even though they share the same sign-in. In Google Cloud "
    "Console, go to APIs & Services -> Library, search for \"Gmail API\", and click Enable. It can "
    "take a few minutes to take effect, then try searching again."
)


def _friendly_error(exc: Exception, fallback: str) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if isinstance(exc, HttpError) and status == 403 and ("accessNotConfigured" in str(exc) or "has not been used" in str(exc)):
        return _API_NOT_ENABLED_MESSAGE
    return f"{fallback}: {exc}"


class GmailService(BaseEmailService):
    provider_name = "google"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def _client_config(self) -> dict:
        return {
            "web": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self._redirect_uri],
            }
        }

    def _flow(self, code_verifier: str | None = None) -> Flow:
        return Flow.from_client_config(
            self._client_config(), scopes=SCOPES, redirect_uri=self._redirect_uri, code_verifier=code_verifier
        )

    def get_authorization_url(self, state: str) -> tuple[str, str | None]:
        flow = self._flow()
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        return url, flow.code_verifier

    def exchange_code_for_tokens(self, code: str, code_verifier: str | None = None) -> EmailTokenSet:
        try:
            flow = self._flow(code_verifier=code_verifier)
            flow.fetch_token(code=code)
            creds = flow.credentials
            email = self._fetch_account_email(creds)
            return EmailTokenSet(
                access_token=creds.token,
                refresh_token=creds.refresh_token,
                expiry=creds.expiry,
                scope=" ".join(creds.scopes or []),
                account_email=email,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a domain error
            raise EmailAuthError(f"Google did not accept the authorization code: {exc}") from exc

    def refresh_access_token(self, refresh_token: str) -> EmailTokenSet:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=SCOPES,
            )
            creds.refresh(GoogleAuthRequest())
            return EmailTokenSet(
                access_token=creds.token,
                refresh_token=creds.refresh_token or refresh_token,
                expiry=creds.expiry,
                scope=" ".join(creds.scopes or []),
            )
        except Exception as exc:  # noqa: BLE001
            raise EmailAuthError(f"Could not refresh the Gmail access token: {exc}") from exc

    def _fetch_account_email(self, creds: Credentials) -> str | None:
        try:
            service = build("oauth2", "v2", credentials=creds)
            info = service.userinfo().get().execute()
            return info.get("email")
        except Exception:  # noqa: BLE001 - non-critical, just a display label
            return None

    @staticmethod
    def _build_query(*, from_query: str | None, subject_query: str | None, date_from: date | None, date_to: date | None) -> str:
        parts = []
        if from_query:
            parts.append(f"from:{from_query}")
        if subject_query:
            parts.append(f'subject:"{subject_query}"')
        if date_from:
            parts.append(f"after:{date_from.strftime('%Y/%m/%d')}")
        if date_to:
            # Gmail's "before:" excludes the given day itself, so push it
            # one day out to make date_to an inclusive upper bound.
            parts.append(f"before:{(date_to + timedelta(days=1)).strftime('%Y/%m/%d')}")
        return " ".join(parts)

    def search_messages(
        self, access_token: str, *, from_query: str | None = None, subject_query: str | None = None,
        date_from: date | None = None, date_to: date | None = None, limit: int = 25,
    ) -> list[EmailMessage]:
        query = self._build_query(from_query=from_query, subject_query=subject_query, date_from=date_from, date_to=date_to)
        try:
            creds = Credentials(token=access_token)
            service = build("gmail", "v1", credentials=creds)
            list_kwargs = {"userId": "me", "maxResults": max(1, min(limit, 50))}
            if query:
                list_kwargs["q"] = query
            result = service.users().messages().list(**list_kwargs).execute()
            refs = result.get("messages", [])

            messages = []
            for ref in refs:
                detail = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
                    .execute()
                )
                messages.append(self._to_email_message(detail))
            return messages
        except Exception as exc:  # noqa: BLE001
            raise EmailFetchError(_friendly_error(exc, "Could not search Gmail")) from exc

    def get_message(self, access_token: str, message_id: str) -> EmailMessage:
        try:
            creds = Credentials(token=access_token)
            service = build("gmail", "v1", credentials=creds)
            detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            return self._to_email_message(detail, include_body=True)
        except Exception as exc:  # noqa: BLE001
            raise EmailFetchError(_friendly_error(exc, "Could not fetch that email from Gmail")) from exc

    def _to_email_message(self, detail: dict, include_body: bool = False) -> EmailMessage:
        payload = detail.get("payload", {}) or {}
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

        sender_name, sender_email = parseaddr(headers.get("from", ""))
        received_at = None
        if headers.get("date"):
            try:
                received_at = parsedate_to_datetime(headers["date"]).replace(tzinfo=None)
            except (ValueError, TypeError):
                received_at = None

        thread_id = detail.get("threadId", detail["id"])
        return EmailMessage(
            external_message_id=detail["id"],
            thread_id=thread_id,
            subject=headers.get("subject") or "(No subject)",
            sender_name=sender_name or None,
            sender_email=sender_email or None,
            received_at=received_at,
            snippet=detail.get("snippet", ""),
            body_text=self._extract_body(payload) if include_body else None,
            web_link=f"https://mail.google.com/mail/u/0/#all/{thread_id}",
        )

    def _extract_body(self, payload: dict) -> str | None:
        """Depth-first search for the first text/plain part. Gmail nests
        multipart messages arbitrarily deep (plain+html alternatives,
        inline attachments, etc.) — plain text is what we want for a task/
        project description, never raw HTML markup.
        """
        def walk(part: dict) -> str | None:
            data = (part.get("body") or {}).get("data")
            if part.get("mimeType") == "text/plain" and data:
                padded = data + "=" * (-len(data) % 4)
                return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            for sub in part.get("parts") or []:
                found = walk(sub)
                if found:
                    return found
            return None

        return walk(payload)
