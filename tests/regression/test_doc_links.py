"""
The README and the pages in docs/ link to files in the repo (code, SQL,
fixtures, other pages) and to headings. Every relative link must still point
at something that exists, so a rename cannot leave a page pointing nowhere.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _anchor(heading: str) -> str:
    # GitHub's rule: lowercase, drop punctuation, spaces become hyphens
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _anchors(page: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", page.read_text(encoding="utf-8"), flags=re.DOTALL)
    return {_anchor(m) for m in re.findall(r"^#+ (.+)$", text, re.MULTILINE)}


def _links() -> list:
    params = []
    for page in PAGES:
        for target in _LINK.findall(page.read_text(encoding="utf-8")):
            if not target.startswith(("http://", "https://", "mailto:")):
                params.append(pytest.param(page, target, id=f"{page.relative_to(ROOT)}->{target}"))
    return params


@pytest.mark.parametrize(("page", "target"), _links())
def test_relative_link_resolves(page, target):
    path, _, fragment = target.partition("#")
    resolved = (page.parent / path).resolve() if path else page

    assert resolved.exists(), f"{target} does not exist"
    if fragment:
        assert fragment in _anchors(resolved), f"no heading #{fragment} in {resolved.name}"
