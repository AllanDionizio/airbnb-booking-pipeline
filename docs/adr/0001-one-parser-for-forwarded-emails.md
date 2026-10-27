# 1. One parser for direct and forwarded emails

**Status:** Accepted

## Context

Airbnb's emails do not always reach the mailbox straight from Airbnb: some arrive forwarded through Gmail. When the Gmail source of this rewrite first ran against a real test mailbox (read-only, printing counts only), forwarded cancellations came back as `UNRECOGNIZED` and forwarded confirmations as `PARSE_ERROR`. The test suite had a forwarded email too, and it passed, because that email had been written from an assumption: the original text with a "Forwarded message" header on top.

A script outside the repo printed the shape of the real forwards with every word outside Airbnb's template masked. Gmail does not forward the original `text/plain` part. It writes a new one from the HTML, and the layout changes:

- headings keep only their first capital ("Código de confirmação" instead of "CÓDIGO DE CONFIRMAÇÃO");
- check-in and checkout are stacked instead of side by side;
- each payout label sits on its own line, with its amount on the next one;
- lines end in CRLF;
- the subject starts with `Fwd:`, so routing by subject missed cancellations, and the cancellation heading in the body is "Reserva cancelada" rather than "RESERVA CANCELADA".

## Decision

Keep one parser per email type and make it read both layouts:

- each heading regex lists both spellings explicitly (`(?:CÓDIGO DE CONFIRMAÇÃO|Código de confirmação)`);
- a stacked-dates regex sits next to the side-by-side one;
- the payout block is read by a line reader that pairs a label with an amount on the same line or on the line below.

[`pipeline.classify`](../../src/airbnb_booking_pipeline/pipeline.py) strips `Fwd:`, `FW:` and `Enc:` prefixes (repeated ones too) before matching the subject. It accepts the cancellation heading in either spelling, but only on a line of its own.

The forwarded fixtures ([`tests/fixtures/confirmations/forwarded.json`](../../tests/fixtures/confirmations/forwarded.json), [`tests/fixtures/cancellations/forwarded_penalty.json`](../../tests/fixtures/cancellations/forwarded_penalty.json)) copy the structure of a real forward, CRLF included, with synthetic content written from scratch.

## Alternatives considered

- **A separate parser for forwards, chosen by detecting the layout.** The differences between the layouts are local: a heading's spelling, where an amount sits, how the dates are laid out. The payout rules (which label feeds which field, with which sign, and the check that the lines add up to the total) would have been written twice, and that is the code that decides what the host is paid.
- **Parse the HTML part instead.** The HTML probably changes less between direct and forwarded emails. But it would mean rewriting a parser that already worked in production on the main case, to fix a side case. It stays the option for a third layout.
- **`re.IGNORECASE` instead of listing both spellings.** Ignoring case would let a heading match inside any sentence that happens to contain the same words. The two spellings, written out, document the two layouts and match nothing else.

## Consequences

- The payout rules live in one place and are tested on both layouts. A test of the per-night adjustment's sign covers the stacked layout too, after a deliberately broken version of that rule (the sign dropped) passed the first round of tests.
- Every heading regex is a little longer, and a third layout would add a third spelling to each, which is the point to reconsider the HTML.
- A forward carries the date it was forwarded, not the date Airbnb sent it. The parser infers the stay's year from that date and the pipeline picks the PTAX day from it, so a late forward can land on the wrong year or rate.
- The default Gmail search looks for Airbnb's sender address and does not match forwards. Both points are listed under [Known limitations](../../README.md#known-limitations).
