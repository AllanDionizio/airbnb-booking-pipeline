"""Where the emails come from. The pipeline only sees `Email` objects."""

from .base import Email, Source, from_rfc822
from .gmail import GmailCredentials, GmailError, GmailSource
from .local import LocalSource

__all__ = [
    "Email",
    "GmailCredentials",
    "GmailError",
    "GmailSource",
    "LocalSource",
    "Source",
    "from_rfc822",
]
