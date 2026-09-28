"""CLI smoke tests: the full command surface of 10.1 (T-CLI-5).

Trampas neutralizadas: T-CLI-5, T-CLI-2, T-CLI-4, T-CLI-1. Regla: 10.1, 10.2.
"""

import pytest
import typer
from typer.testing import CliRunner

from tikdown_rs.cli.main import app
from tikdown_rs.core.errors import ConfigurationError

# The plan table (10.1) is the SPEC, written literally. 29 commands total.
COMMAND_TABLE = {
    "daemon": ["run", "stop", "status", "selfcheck", "healthcheck"],
    "monitor": ["start", "stop"],
    "accounts": ["add", "list", "pause", "resume", "notify", "remove", "check", "stats"],
    "backfill": ["run", "status", "cancel", "retry-failed"],
    "cookies": ["add", "list", "test", "remove"],
    "videos": ["last", "export", "integrity"],
    "system": ["disk", "backup", "site render"],
}

# Key flags from 10.1 that must appear in the corresponding --help output.
EXPECTED_FLAGS = {
    ("accounts", "add"): ["--mode", "--then-monitor"],
    ("accounts", "notify"): ["--on", "--off"],
    ("backfill", "run"): ["--queue"],
    ("backfill", "retry-failed"): ["--all"],
    # M21: adopted real flag already used in production rounds (labels like
    # 'live-round'); added to the 10.1 tree.
    ("cookies", "add"): ["--keep-source", "--label"],
    ("videos", "export"): ["--format"],
    ("system", "disk"): ["--resume"],
}

runner = CliRunner()


def _captured(result):
    """stdout + stderr across click versions (mix_stderr differences)."""
    try:
        return result.output + result.stderr
    except ValueError:  # click < 8.2: stderr already mixed into output
        return result.output


@pytest.mark.parametrize(
    ("group", "command"),
    [(group, cmd) for group, commands in COMMAND_TABLE.items() for cmd in commands],
    ids=lambda c: c,
)
def test_every_command_has_reachable_help(group: str, command: str) -> None:
    """T-CLI-5: every 10.1 command is a real, reachable @app.command() via --help."""
    argv = [group, *command.split(), "--help"]
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, f"{group} {command} --help failed: {result.output}"
    assert "Usage:" in result.output


@pytest.mark.parametrize(("group", "command"), sorted(EXPECTED_FLAGS))
def test_help_shows_declared_flags(group: str, command: str) -> None:
    result = runner.invoke(app, [group, *command.split(), "--help"])
    assert result.exit_code == 0
    for flag in EXPECTED_FLAGS[(group, command)]:
        assert flag in result.output


def test_accounts_notify_accepts_an_optional_user_argument() -> None:
    """M21: adopted real usage — `accounts notify` toggles one account with a
    handle, or ALL accounts when it is omitted (10.1 tree: `notify [@user]`)."""
    result = runner.invoke(app, ["accounts", "notify", "--help"])
    assert result.exit_code == 0
    assert "[user]" in result.output


def test_registered_groups_match_10_1_exactly() -> None:
    """The typer tree is 1:1 with the 10.1 table: no missing groups, no extras."""
    registered = set()
    for group in app.registered_groups:
        name = group.name or group.typer_instance.info.name
        if name:
            registered.add(name)
    assert registered == set(COMMAND_TABLE)


def test_bare_help_exits_zero_without_runtime_error() -> None:
    """T-CLI-2: bare --help does not raise 'Could not get a command'."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Could not get a command" not in result.output


def test_version_flag_prints_name_and_version() -> None:
    from tikdown_rs import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"tikdown-rs {__version__}" in result.output


def test_run_or_exit_converts_configuration_error(capsys) -> None:
    """T-CLI-4: ConfigurationError -> 'ERROR <msg>' + exit 1, no traceback."""
    from tikdown_rs.cli.common import run_or_exit

    def boom() -> None:
        raise ConfigurationError("boom")

    with pytest.raises(typer.Exit) as excinfo:
        run_or_exit(boom)
    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    assert "ERROR boom" in captured.err


def test_run_or_exit_converts_business_error(capsys) -> None:
    """M7/T-CLI-8: DownloadTimeoutError exits cleanly, same funnel style."""
    from tikdown_rs.cli.common import run_or_exit
    from tikdown_rs.core.errors import DownloadTimeoutError

    def slow() -> None:
        raise DownloadTimeoutError("video exceeded the timeout")

    with pytest.raises(typer.Exit) as excinfo:
        run_or_exit(slow)
    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    assert "ERROR video exceeded the timeout" in captured.err


def test_run_or_exit_converts_operational_error(capsys) -> None:
    """M7/T-CLI-8: a locked SQLite DB at the CLI boundary exits cleanly, no traceback."""
    from sqlalchemy.exc import OperationalError

    from tikdown_rs.cli.common import run_or_exit

    def locked() -> None:
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    with pytest.raises(typer.Exit) as excinfo:
        run_or_exit(locked)
    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    assert "ERROR database is locked" in captured.err


def test_run_or_exit_does_not_catch_unexpected_exceptions() -> None:
    """M7: programming errors are never caught — bare Exception stays out of the funnel."""
    from tikdown_rs.cli.common import run_or_exit

    def broken() -> None:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        run_or_exit(broken)


def test_run_or_exit_returns_value() -> None:
    from tikdown_rs.cli.common import run_or_exit

    assert run_or_exit(lambda a, b: a + b, 2, 3) == 5
