"""A fake Gmail API, shared by the unit and the integration tests. No network."""

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from airbnb_booking_pipeline.sources import GmailCredentials, GmailSource

CREDENTIALS = GmailCredentials("test-client-id", "test-client-secret", "test-refresh-token")
ACCESS_TOKEN = "test-access-token"
# Gmail's internalDate: when the mailbox received the email
RECEIVED = datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


@dataclass
class FakeGmail:
    """
    The token endpoint, the message list and the message download, as the
    Gmail API answers them. `messages` maps a Gmail id to its RFC 822 bytes,
    in the order the list returns them.
    """

    messages: dict[str, bytes]
    page_size: int = 500
    refuse_token: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def source(self, **options) -> GmailSource:
        http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return GmailSource(CREDENTIALS, http=http, **options)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            if self.refuse_token:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Token has been expired or revoked.",
                    },
                )
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3599})

        if request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(401)
        gmail_id = request.url.path.removeprefix("/gmail/v1/users/me/messages").strip("/")
        return self._get(gmail_id, request) if gmail_id else self._list(request)

    def _list(self, request: httpx.Request) -> httpx.Response:
        ids = list(self.messages)
        start = int(request.url.params.get("pageToken", "0"))
        page: dict = {"resultSizeEstimate": len(ids)}
        if chunk := ids[start : start + self.page_size]:  # no "messages" key when empty
            page["messages"] = [{"id": i, "threadId": i} for i in chunk]
        if start + self.page_size < len(ids):
            page["nextPageToken"] = str(start + self.page_size)
        return httpx.Response(200, json=page)

    def _get(self, gmail_id: str, request: httpx.Request) -> httpx.Response:
        if gmail_id not in self.messages:
            return httpx.Response(404)
        if request.url.params.get("format") != "raw":
            return httpx.Response(400)
        # base64url without its padding, so the source has to restore it
        raw = base64.urlsafe_b64encode(self.messages[gmail_id]).decode().rstrip("=")
        internal_date = str(int(RECEIVED.timestamp() * 1000))
        return httpx.Response(200, json={"id": gmail_id, "internalDate": internal_date, "raw": raw})


@pytest.fixture
def fake_gmail():
    """FakeGmail itself: `fake_gmail({id: raw}).source()` is a GmailSource that talks to it."""
    return FakeGmail
