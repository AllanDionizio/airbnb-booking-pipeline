"""
Command line errors: bad arguments, an inbox or Gmail that cannot be read,
and a database that cannot be reached.
"""

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


def test_takes_either_an_inbox_or_gmail(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--inbox", str(tmp_path), "--gmail", "--database-url", NO_DATABASE])

    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_gmail_without_credentials_fails_before_connecting(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--gmail", "--database-url", NO_DATABASE])

    assert exit_info.value.code == 2
    assert "GOOGLE_REFRESH_TOKEN" in capsys.readouterr().err


def test_gmail_failing_to_answer_exits_with_1_and_one_line(monkeypatch, fake_gmail, capsys):
    gmail = fake_gmail({}, refuse_token=True)
    monkeypatch.setattr(cli.GmailSource, "from_env", gmail.source)

    exit_code = cli.main(["run", "--gmail", "--database-url", NO_DATABASE])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert err.startswith("airbnb-pipeline: Google refused the refresh token")
    assert err.count("\n") == 1


@pytest.mark.parametrize(
    ("name", "value"), [("BUSINESS_TZ", "America/Sao Paulo"), ("USD_BRL_FALLBACK", "abc")]
)
def test_an_optional_setting_that_does_not_parse_fails_before_connecting(
    tmp_path, monkeypatch, capsys, name, value
):
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--inbox", str(tmp_path), "--database-url", NO_DATABASE])

    assert exit_info.value.code == 2
    assert name in capsys.readouterr().err
