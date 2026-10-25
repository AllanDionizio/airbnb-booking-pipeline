"""Command line errors: bad arguments, and a database that cannot be reached."""

import pytest

from airbnb_booking_pipeline import cli

# Nothing listens here: any attempt to connect would fail the test
NO_DATABASE = "postgresql://nobody@127.0.0.1:1/none"


def test_requires_a_database_url(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--inbox", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_a_missing_inbox_fails_before_connecting(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--inbox", str(tmp_path / "missing"), "--database-url", NO_DATABASE])

    assert exit_info.value.code == 2
    assert "Inbox directory not found" in capsys.readouterr().err


def test_an_unreachable_database_exits_with_1(tmp_path, capsys):
    exit_code = cli.main(["run", "--inbox", str(tmp_path), "--database-url", NO_DATABASE])

    assert exit_code == 1
    assert "database error" in capsys.readouterr().err
