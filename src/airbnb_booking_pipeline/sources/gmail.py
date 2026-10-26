"""
The host's Gmail mailbox, read through the Gmail REST API.

Each email is downloaded in Gmail's "raw" format, the whole RFC 822 message,
and read by the same `from_rfc822` as an .eml file. So the Message-ID header
stays the idempotency key: an email already loaded from a file is not loaded
again when it comes from Gmail.

Access is read-only (gmail.readonly scope), through an OAuth refresh token
obtained once, outside this repo. Every fetch exchanges it for a short-lived
access token.
"""

import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .base import Email, from_rfc822

log = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_PAGE_SIZE = 500  # the most the list endpoint returns per page
_TIMEOUT = 30.0

# Airbnb's confirmation and cancellation emails from the last two days. A
# daily run reads every email twice, and that costs nothing: the second time
# it comes back as ALREADY_PROCESSED. A one-day window would lose the emails
# that arrive while a run is late.
DEFAULT_QUERY = (
    "from:automated@airbnb.com newer_than:2d "
    '(subject:"Reserva confirmada" OR subject:"Cancelado: reserva")'
)
DEFAULT_LIMIT = 500

# The settings GmailSource.from_env reads, all three required
ENV_CREDENTIALS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


class GmailError(RuntimeError):
    """Gmail could not be read: credentials, network, or a query that matches too much."""


@dataclass(frozen=True)
class GmailCredentials:
    client_id: str
    # Kept out of repr, so they never reach a log line or a traceback
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


class GmailSource:
    """
    Every email that matches `query`, as Gmail's search box would find it.

    The listing follows every page. When more than `limit` emails match,
    the fetch fails instead of reading only some of them: a run that stops
    is visible, while emails left behind are lost without a trace.
    """

    def __init__(
        self,
        credentials: GmailCredentials,
        query: str = DEFAULT_QUERY,
        http: httpx.Client | None = None,
        limit: int = DEFAULT_LIMIT,
    ):
        self.credentials = credentials
        self.query = query
        self.limit = limit
        self._http = http or httpx.Client(timeout=_TIMEOUT)

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> "GmailSource":
        """Credentials from GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN."""
        missing = [name for name in ENV_CREDENTIALS if not os.environ.get(name)]
        if missing:
            raise ValueError(f"Gmail needs {', '.join(missing)} in the environment")
        credentials = GmailCredentials(*(os.environ[name] for name in ENV_CREDENTIALS))
        return cls(credentials, query=os.environ.get("GMAIL_QUERY") or DEFAULT_QUERY, http=http)

    def fetch(self) -> list[Email]:
        try:
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            ids = self._list_ids(headers)
            log.info("%d emails match the Gmail query %r", len(ids), self.query)
            return [self._get(gmail_id, headers) for gmail_id in ids]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GmailError(f"Gmail could not be read: {type(exc).__name__}: {exc}") from exc

    def _access_token(self) -> str:
        response = self._http.post(
            _TOKEN_URL,
            data={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "refresh_token": self.credentials.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.is_error:
            # Google names the reason ("invalid_grant": the refresh token was
            # revoked or expired); the response never echoes the secrets back.
            raise GmailError(f"Google refused the refresh token: {response.text}")
        return response.json()["access_token"]

    def _list_ids(self, headers: dict[str, str]) -> list[str]:
        ids: list[str] = []
        params = {"q": self.query, "maxResults": _PAGE_SIZE}
        while True:
            response = self._http.get(_MESSAGES_URL, params=params, headers=headers)
            response.raise_for_status()
            page = response.json()
            ids += [message["id"] for message in page.get("messages", [])]  # absent when none
            if len(ids) > self.limit:
                raise GmailError(
                    f"More than {self.limit} emails match {self.query!r}: narrow the query"
                )
            if "nextPageToken" not in page:
                return ids
            params["pageToken"] = page["nextPageToken"]

    def _get(self, gmail_id: str, headers: dict[str, str]) -> Email:
        response = self._http.get(
            f"{_MESSAGES_URL}/{gmail_id}", params={"format": "raw"}, headers=headers
        )
        response.raise_for_status()
        message = response.json()
        raw = message["raw"]
        # base64url; Gmail may leave out the "=" padding, so it is put back
        rfc822 = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        # When Gmail received it, in milliseconds: the date when the header has none
        received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=UTC)
        return from_rfc822(rfc822, default_id=f"gmail:{gmail_id}", default_date=received)
