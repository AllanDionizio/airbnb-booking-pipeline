"""
docs/sample-email.md walks through two fixtures line by line. Every quoted
Portuguese line must still be in its fixture, and the parser output shown
must be the fixture's expected output, so the page cannot drift from the
emails the regression suite runs on.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DOC = ROOT / "docs" / "sample-email.md"

_FIXTURE_LINK = re.compile(r"\(\.\./(tests/fixtures/[\w/]+\.json)\)")
_QUOTED_LINE = re.compile(r"^\| `([^`]+)` \|", re.MULTILINE)
_JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.DOTALL)


def _flat(text: str) -> str:
    # Columns, wrapped lines, nbsp and thin spaces all become one space
    return " ".join(text.split())


def _sections() -> list:
    sections = DOC.read_text(encoding="utf-8").split("\n## ")[1:]
    return [pytest.param(section, id=section.splitlines()[0]) for section in sections]


@pytest.mark.parametrize("section", _sections())
def test_section_quotes_its_fixture(section):
    fixture = json.loads((ROOT / _FIXTURE_LINK.search(section)[1]).read_text(encoding="utf-8"))
    email = _flat(fixture["subject"] + "\n" + fixture["body"])
    quoted = _QUOTED_LINE.findall(section)

    assert quoted
    for line in quoted:
        assert _flat(line) in email, f"not in the fixture: {line!r}"
    assert json.loads(_JSON_BLOCK.search(section)[1]) == fixture["expected"]
