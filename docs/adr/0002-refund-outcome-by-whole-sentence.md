# 2. Tell a full refund from a penalty by the whole sentence

**Status:** Accepted

## Context

A cancellation email has no payout block. One sentence, after "Conforme sua política de cancelamento," ("Under your cancellation policy,"), says what happened to the money:

- "o hóspede recebeu um reembolso integral" ("the guest received a full refund"): the host keeps nothing;
- "seu recebimento foi atualizado para R$X" ("your payout was updated to R$X"): the guest paid a penalty of X.

The words "reembolso integral" ("full refund") also appear in every email that carries a penalty. They are the label of a button, "Emitir reembolso integral" ("Issue full refund"), which lets the host refund the guest anyway. A parser that looks for "reembolso integral" anywhere in the email reads every penalty as a full refund. The penalty disappears from the records with no error, and penalties are the only income a cancellation brings in.

This turned up while reading real cancellation emails before the first parser was written, together with a second detail of the real format: the body is wrapped at about 60 columns, and newer emails put a non-breaking space between the currency symbol and the amount.

## Decision

[`parse_cancellation`](../../src/airbnb_booking_pipeline/parsers/cancellation.py) works on the flattened body, where runs of spaces, line breaks and non-breaking spaces become one space. On that text:

1. It looks for the penalty sentence first. The penalty sentence never appears in a full-refund email, so a match settles the outcome.
2. Otherwise it looks for the full-refund sentence, anchored on "Conforme sua política de cancelamento," and never on the bare words.
3. If neither sentence is there, the outcome is `UNDETERMINED`: the cancellation is still recorded, and a person reads it. Only a missing booking code raises a `ParseError`, because then there is nothing to record.

Steps 1 and 2 are two layers against the same trap. Either one alone reads today's emails correctly: the anchored refund sentence never matches a penalty email, and testing the penalty first would win even over a looser refund match. Both stay, so a change in Airbnb's wording has to get past two of them before a penalty is misread.

A full refund carries no amount and no currency. The email does not state one, and the booking already knows its own.

## Alternatives considered

- **Match the words "reembolso integral" anywhere.** This is the false positive described above.
- **Check for the refund first and exclude the button's label.** Correctness would then depend on the button's wording, and on there being no other place the words show up. Anchoring on the outcome sentence depends only on the sentence that states the outcome.
- **Raise an error when the sentence is not recognized.** The cancellation would be lost along with the money detail. An `UNDETERMINED` cancellation still flags its booking, and the run report lists it for a person.

## Consequences

- Four regression fixtures carry the button: [`penalty_compact`](../../tests/fixtures/cancellations/penalty_compact.json), `penalty_nbsp`, `penalty_usd` and `forwarded_penalty`. Removing both layers fails all four, along with a unit test that puts the button next to a penalty in three amount formats.
- [`docs/sample-email.md`](../sample-email.md) shows the button line by line next to the sentence that actually decides the outcome.
- If Airbnb rewords the outcome sentence, cancellations come back `UNDETERMINED` and show up in the run report. They are not misread silently.
