"""Where the emails come from. The pipeline only sees `Email` objects."""

from .base import Email, Source, from_rfc822
from .local import LocalSource

__all__ = ["Email", "LocalSource", "Source", "from_rfc822"]
