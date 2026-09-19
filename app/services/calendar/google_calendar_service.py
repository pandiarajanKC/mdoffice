"""Google Calendar implementation of BaseCalendarService.

Requires GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
GOOGLE_OAUTH_REDIRECT_URI to be configured (see README) — until then,
CalendarService.is_configured() returns False and the UI shows a setup
notice instead of a broken "Connect" button.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google sometimes grants a superset of requested scopes

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.services.calendar.base_calendar_service import (
    BaseCalendarService,
    CalendarAuthError,
    CalendarEvent,
    CalendarSyncError,
    TokenSet,
)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "openid", "https://www.googleapis.com/auth/userinfo.email"]


class GoogleCalendarService(BaseCalendarService):
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
            access_type="offline",  # required to receive a refresh_token
            include_granted_scopes="true",
            prompt="consent",  # forces a refresh_token even on repeat connects
            state=state,
        )
        # authorization_url() auto-generates a PKCE code_verifier and embeds its
        # code_challenge in the URL; the callback's token exchange must reuse this
        # exact verifier (a fresh Flow instance can't recover it), so hand it back
        # to the caller to persist across the redirect.
        return url, flow.code_verifier

    def exchange_code_for_tokens(self, code: str, code_verifier: str | None = None) -> TokenSet:
        try:
            flow = self._flow(code_verifier=code_verifier)
            flow.fetch_token(code=code)
            creds = flow.credentials
            email = self._fetch_account_email(creds)
            return TokenSet(
                access_token=creds.token,
                refresh_token=creds.refresh_token,
                expiry=creds.expiry,
                scope=" ".join(creds.scopes or []),
                account_email=email,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a domain error
            raise CalendarAuthError(f"Google did not accept the authorization code: {exc}") from exc

    def refresh_access_token(self, refresh_token: str) -> TokenSet:
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
            return TokenSet(
                access_token=creds.token,
                refresh_token=creds.refresh_token or refresh_token,
                expiry=creds.expiry,
                scope=" ".join(creds.scopes or []),
            )
        except Exception as exc:  # noqa: BLE001
            raise CalendarAuthError(f"Could not refresh the Google access token: {exc}") from exc

    def _fetch_account_email(self, creds: Credentials) -> str | None:
        try:
            service = build("oauth2", "v2", credentials=creds)
            info = service.userinfo().get().execute()
            return info.get("email")
        except Exception:  # noqa: BLE001 - non-critical, just a display label
            return None

    def list_events(self, access_token: str, *, time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
        try:
            creds = Credentials(token=access_token)
            service = build("calendar", "v3", credentials=creds)
            result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min.astimezone(timezone.utc).isoformat(),
                    timeMax=time_max.astimezone(timezone.utc).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise CalendarSyncError(f"Could not fetch events from Google Calendar: {exc}") from exc

        return [self._to_calendar_event(item) for item in result.get("items", []) if "start" in item]

    def _to_calendar_event(self, item: dict) -> CalendarEvent:
        start = self._parse_datetime(item["start"])
        end = self._parse_datetime(item.get("end", item["start"]))

        attendees = item.get("attendees") or []
        attendees_text = ", ".join(a.get("displayName") or a.get("email", "") for a in attendees) or None

        # A separate emails-only list — attendees_text above prefers a
        # display name when Google provides one, which reads better but
        # isn't usable for actually addressing an email.
        emails = [a["email"] for a in attendees if a.get("email")]
        organizer_info = item.get("organizer") or {}
        if organizer_info.get("email") and organizer_info["email"] not in emails:
            emails.append(organizer_info["email"])
        attendees_emails = ", ".join(emails) or None

        meeting_link = item.get("hangoutLink")
        if not meeting_link:
            for entry in (item.get("conferenceData", {}) or {}).get("entryPoints", []):
                if entry.get("entryPointType") == "video":
                    meeting_link = entry.get("uri")
                    break

        organizer = organizer_info.get("displayName") or organizer_info.get("email")

        return CalendarEvent(
            external_event_id=item["id"],
            title=item.get("summary") or "(No title)",
            description=item.get("description"),
            start_datetime=start,
            end_datetime=end,
            location=item.get("location"),
            organizer=organizer,
            attendees_text=attendees_text,
            attendees_emails=attendees_emails,
            meeting_link=meeting_link,
        )

    @staticmethod
    def _parse_datetime(node: dict) -> datetime:
        if "dateTime" in node:
            return datetime.fromisoformat(node["dateTime"].replace("Z", "+00:00")).replace(tzinfo=None)
        # All-day event: only a date is given.
        return datetime.fromisoformat(node["date"])
