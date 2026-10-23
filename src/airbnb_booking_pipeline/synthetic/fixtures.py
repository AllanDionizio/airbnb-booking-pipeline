"""Serialize the scenarios into the JSON regression fixtures under tests/fixtures/."""

import json
from pathlib import Path

from .emails import RenderedEmail, render_cancellation, render_confirmation
from .scenarios import CANCELLATIONS, CONFIRMATIONS


def _fixture(name: str, description: str, email: RenderedEmail, expected: dict) -> str:
    data = {
        "scenario": name,
        "description": description,
        "subject": email.subject,
        "email_date": email.email_date.isoformat(),
        "body": email.body,
        "expected": expected,
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def build_fixtures() -> dict[str, str]:
    """Relative path ('confirmations/simple.json') → file contents, for all 18 scenarios."""
    files = {}
    for sc in CONFIRMATIONS:
        spec = sc.spec
        expected = {
            "confirmation_code": spec.code,
            "guest_name": spec.guest,
            "check_in": spec.check_in.isoformat(),
            "check_out": spec.check_out.isoformat(),
            "num_guests": spec.adults,
            "listing_name": spec.listing.name,
            "currency": spec.currency,
            "payout_lines": sc.expected_payout_lines,
            "host_payout": spec.host_payout,
        }
        files[f"confirmations/{sc.name}.json"] = _fixture(
            sc.name, sc.description, render_confirmation(spec), expected
        )
    for sc in CANCELLATIONS:
        expected = {
            "confirmation_code": sc.spec.code,
            "outcome": sc.expected_outcome,
            "penalty_amount": sc.expected_penalty,
            "currency": sc.expected_currency,
        }
        files[f"cancellations/{sc.name}.json"] = _fixture(
            sc.name, sc.description, render_cancellation(sc.spec), expected
        )
    return files


def write_fixtures(root: Path) -> list[Path]:
    """Write every fixture and delete the ones whose scenario no longer exists."""
    files = build_fixtures()
    for path in root.rglob("*.json"):
        if path.relative_to(root).as_posix() not in files:
            path.unlink()

    written = []
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        written.append(path)
    return written
