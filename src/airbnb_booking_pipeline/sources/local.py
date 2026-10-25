"""A directory of .eml files: the demo inbox and the tests use it in place of Gmail."""

from pathlib import Path

from .base import Email, from_rfc822


class LocalSource:
    """Every *.eml file directly inside `inbox`, in file name order."""

    def __init__(self, inbox: Path):
        self.inbox = Path(inbox)

    def fetch(self) -> list[Email]:
        if not self.inbox.is_dir():
            raise FileNotFoundError(f"Inbox directory not found: {self.inbox}")
        return [
            from_rfc822(path.read_bytes(), default_id=path.name)
            for path in sorted(self.inbox.glob("*.eml"))
        ]
