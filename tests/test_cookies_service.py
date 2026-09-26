"""Services for the cookie store: add/list/remove.

Trampas neutralizadas: T-COOKIES-8. Regla: 7, 3.4.
"""

import json
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tikdown_rs.cli.main import app
from tikdown_rs.core.cookie_parser import HEADER
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.errors import ConfigurationError
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie
from tikdown_rs.services.cookies import add_cookie, list_cookies, remove_cookie

JSON_COOKIES = json.dumps(
    [
        {"name": "sid_tt", "value": "abc123", "domain": ".tiktok.com", "path": "/"},
    ]
)

runner = CliRunner()


@pytest.fixture
async def migrated_factory(tmp_path: Path):
    """Engine + session factory over a REAL file DB migrated with run_migrations."""
    import asyncio

    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def _write_source(tmp_path: Path, name: str = "cookies.json") -> Path:
    source = tmp_path / name
    source.write_text(JSON_COOKIES, encoding="utf-8")
    return source


# --- add_cookie ---


async def test_add_persists_canonical_blob_and_deletes_source(
    migrated_factory, tmp_path: Path
) -> None:
    source = _write_source(tmp_path)
    cookie_id = await add_cookie(migrated_factory, source, "main")

    async with migrated_factory() as session:
        row = await session.get(Cookie, cookie_id)
    assert row is not None
    blob = row.cookie_blob.decode("utf-8")
    assert blob.splitlines()[0] == HEADER
    assert "sid_tt" in blob
    assert row.validation_state == "inconclusive"
    assert row.label == "main"
    assert row.created_at is not None
    assert row.updated_at is not None
    assert not source.exists()


async def test_add_keep_source_keeps_file(migrated_factory, tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    await add_cookie(migrated_factory, source, None, keep_source=True)
    assert source.exists()


async def test_add_missing_source_raises_configuration_error(
    migrated_factory, tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError, match="missing.json"):
        await add_cookie(migrated_factory, tmp_path / "missing.json", None)


async def test_add_undeletable_source_still_succeeds_with_warning(
    migrated_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    source = _write_source(tmp_path)

    def deny(self, *args, **kwargs):
        raise PermissionError("simulated locked file")

    monkeypatch.setattr(Path, "unlink", deny)
    with caplog.at_level(logging.WARNING, logger="tikdown_rs.services.cookies"):
        cookie_id = await add_cookie(migrated_factory, source, None)
    assert cookie_id == 1  # T-COOKIES-8: success continues after deletion failure
    assert any("tikdown_rs.services.cookies" == r.name for r in caplog.records)


# --- list_cookies ---


async def test_list_returns_rows_with_declared_fields(migrated_factory, tmp_path: Path) -> None:
    await add_cookie(migrated_factory, _write_source(tmp_path), "main")
    rows = await list_cookies(migrated_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == 1
    assert row.label == "main"
    assert row.validation_state == "inconclusive"
    assert row.last_validated_at is None
    assert hasattr(row, "expiration_date")
    assert row.created_at is not None


async def test_list_empty_returns_empty_list(migrated_factory) -> None:
    assert await list_cookies(migrated_factory) == []


# --- remove_cookie ---


async def test_remove_deletes_the_row(migrated_factory, tmp_path: Path) -> None:
    cookie_id = await add_cookie(migrated_factory, _write_source(tmp_path), None)
    await remove_cookie(migrated_factory, cookie_id)
    assert await list_cookies(migrated_factory) == []


async def test_remove_unknown_id_raises_configuration_error(migrated_factory) -> None:
    with pytest.raises(ConfigurationError, match="999"):
        await remove_cookie(migrated_factory, 999)


# --- CLI smoke against a tmp DATA_DIR + real DB (10.1, 10.2) ---


def test_cli_cookies_add_list_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _write_source(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["cookies", "add", str(source)])
    assert result.exit_code == 0, result.output
    assert "1" in result.output
    assert not source.exists()

    result = runner.invoke(app, ["cookies", "list"])
    assert result.exit_code == 0, result.output
    assert "state=inconclusive" in result.output

    result = runner.invoke(app, ["cookies", "remove", "1"])
    assert result.exit_code == 0, result.output
    assert "1" in result.output

    result = runner.invoke(app, ["cookies", "list"])
    assert result.exit_code == 0
    assert "no cookies" in result.output


def test_cli_cookies_remove_unknown_id_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["cookies", "remove", "42"])
    assert result.exit_code == 1
    assert "ERROR" in result.output
