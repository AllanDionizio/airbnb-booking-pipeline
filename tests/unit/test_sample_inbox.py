"""The committed demo inbox must be exactly what the generator writes."""

from pathlib import Path

from airbnb_booking_pipeline.synthetic.inbox import build_inbox

INBOX = Path(__file__).parents[2] / "data" / "sample_inbox"


def test_committed_inbox_matches_the_generator():
    committed = {path.name: path.read_bytes() for path in INBOX.glob("*.eml")}

    assert committed == build_inbox(), "the sample inbox is stale: run `make inbox`"
