"""
Parser for Airbnb's "Cancelado: reserva <CODE>" email.

Much simpler than the confirmation parser: the cancellation email has no
payout block, only one sentence saying what happened to the money. Two
variants, both anchored on "Conforme sua política de cancelamento,":

  - "... o hóspede recebeu um reembolso integral."   → full refund
  - "... seu recebimento foi atualizado para R$X."   → penalty of X

⚠️ TRAP: "reembolso integral" (full refund) ALSO appears in the emails WITH
a penalty, in the "Emitir reembolso integral" call to action (the button
that offers to refund everything anyway). So detection is anchored on the
whole sentence, never on a loose substring, and the penalty is tested FIRST.

The body comes wrapped at ~60 columns, so everything runs on the flattened
text (runs of spaces and line breaks become one space). The nbsp (\\xa0)
Airbnb puts between symbol and amount disappears in the flattening too, so
the old format (`R$123,45`) and the new one (`R$\\xa0123,45`) hit the same
regex.

Validated against real production cancellation emails.
"""

import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from ._common import CURRENCY, ParseError, currency_code, flatten, parse_amount

# The confirmation code is in the subject ("Cancelado: reserva HMTEST0001 de
# ...") and in the body ("precisou cancelar a reserva HMTEST0001 de ...").
# The subject is the preferred source; the body is the fallback.
_RE_CODE_SUBJECT = re.compile(r"reserva\s+([A-Z0-9]{8,})")
_RE_CODE_BODY = re.compile(r"cancelar\s+a\s+reserva\s+([A-Z0-9]{8,})")

# The optional suffix ("$ 1.234,56 USD") appears only in the USD format.
_RE_PENALTY = re.compile(
    r"seu recebimento foi atualizado para\s*" + CURRENCY + r"\s*([\d.,]+)(?:\s*(BRL|USD))?"
)
_RE_FULL_REFUND = re.compile(
    r"Conforme sua política de cancelamento,\s*o hóspede recebeu um reembolso integral"
)


class CancellationOutcome(StrEnum):
    FULL_REFUND = "full_refund"
    PENALTY = "penalty"
    UNDETERMINED = "undetermined"


@dataclass
class CancellationNotice:
    confirmation_code: str
    outcome: CancellationOutcome
    penalty_amount: float | None  # in `currency`; None when there is no penalty
    # "BRL" or "USD", the currency of the penalty. None when there is no
    # amount: the email does not say, and the booking already knows.
    currency: str | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


def parse_cancellation(body_text: str, subject: str | None = None) -> CancellationNotice:
    """
    Extract the confirmation code and the money outcome of a cancellation.

    Raises ParseError when the confirmation code is missing (there is nothing
    to record). When the code is there but the money sentence is not
    recognized, returns UNDETERMINED instead of raising: recording the email
    for a human to look at beats losing the cancellation.
    """
    flat_subject = flatten(subject)
    flat_body = flatten(body_text)

    code_match = _RE_CODE_SUBJECT.search(flat_subject) or _RE_CODE_BODY.search(flat_body)
    if code_match is None:
        raise ParseError("Could not extract the confirmation code from the cancellation email")
    code = code_match.group(1)

    # Penalty first: penalty emails also contain the "Emitir reembolso
    # integral" call to action, so checking the refund first would be a
    # false positive.
    penalty_match = _RE_PENALTY.search(flat_body)
    if penalty_match:
        symbol, raw_amount, suffix = penalty_match.groups()
        return CancellationNotice(
            confirmation_code=code,
            outcome=CancellationOutcome.PENALTY,
            penalty_amount=parse_amount(raw_amount),
            currency=suffix or currency_code(symbol),
        )

    outcome = (
        CancellationOutcome.FULL_REFUND
        if _RE_FULL_REFUND.search(flat_body)
        else CancellationOutcome.UNDETERMINED
    )
    return CancellationNotice(
        confirmation_code=code, outcome=outcome, penalty_amount=None, currency=None
    )
