"""
Synthetic Airbnb host emails for tests and demos.

Nothing here comes from a real inbox: the listings are a closed fictional
list, the guests are "Guest <NATO letter>", the booking codes are HMTEST…
and every amount is made up. Only the layout copies Airbnb's templates,
because the layout is what the parsers depend on.

Regenerate the regression fixtures with `make fixtures`.
"""

from .emails import (
    CancellationSpec,
    ConfirmationSpec,
    RenderedEmail,
    format_money,
    render_cancellation,
    render_confirmation,
    to_eml,
)
from .listings import LISTINGS, Listing

__all__ = [
    "LISTINGS",
    "CancellationSpec",
    "ConfirmationSpec",
    "Listing",
    "RenderedEmail",
    "format_money",
    "render_cancellation",
    "render_confirmation",
    "to_eml",
]
