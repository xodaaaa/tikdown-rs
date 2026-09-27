"""services/static_site.render_site: the M6 T6 static dashboard (10.3).

Trampas covered: T-DATA-10 (total_disk_bytes is READ, never recomputed),
no secrets in any output, deterministic timestamps via injected now_fn,
history-limit bounding, command_reference injected as a PARAMETER (the
services/ layer never imports cli/ or typer). Regla: 10.3, 0.4 admission rule.

All doubles are deterministic: migrated real SQLite database, fake clock,
tmp_path output directories.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from tikdown_rs.cli.main import app
from tikdown_rs.core.config import Settings
from tikdown_rs.core.daemon_state import record_last_known_good_ytdlp, register_daemon_start
from tikdown_rs.core.db import create_db_engine, make_session_factory, sqlite_url_for
from tikdown_rs.core.migrations import run_migrations
from tikdown_rs.models import Cookie, MonitoredAccount, Video
from tikdown_rs.services.static_site import (
    NO_COMMAND_REFERENCE_PLACEHOLDER,
    render_site,
    resolve_output_dir,
)

FAKE_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)

OUTPUT_FILES = ("index.html", "accounts.json", "downloads.json", "data.json")


@pytest.fixture
async def migrated_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Session factory over a REAL migrated file database (shared pattern)."""
    await asyncio.to_thread(run_migrations, tmp_path)
    engine = create_db_engine(sqlite_url_for(tmp_path))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


def site_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(data_dir=tmp_path, static_site_history_limit=5, **overrides)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


async def add_account(
    factory: async_sessionmaker[AsyncSession],
    username: str = "acct",
    **overrides,
) -> int:
    async with factory() as session:
        account_kwargs = {"backfill_status": "idle", **overrides}
        account = MonitoredAccount(username=username, **account_kwargs)
        session.add(account)
        await session.commit()
        return account.id


async def add_video(
    factory: async_sessionmaker[AsyncSession],
    account_id: int | None,
    video_id: str,
    *,
    status: str = "downloaded",
    downloaded_at: str = "2025-01-01T00:00:00+00:00",
    file_size: int | None = 1000,
    error_category: str | None = None,
) -> int:
    async with factory() as session:
        video = Video(
            tiktok_video_id=video_id,
            account_id=account_id,
            status=status,
            downloaded_at=downloaded_at if status == "downloaded" else None,
            file_size=file_size if status == "downloaded" else None,
            error_category=error_category,
        )
        session.add(video)
        await session.commit()
        return video.id


# --- file set -----------------------------------------------------------------


async def test_render_writes_exactly_four_files(tmp_path, migrated_factory) -> None:
    await add_account(migrated_factory)
    out = tmp_path / "public"
    paths = await render_site(
        migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW
    )
    assert sorted(p.name for p in out.iterdir()) == sorted(OUTPUT_FILES)
    assert sorted(p.name for p in paths) == sorted(OUTPUT_FILES)


async def test_index_embeds_three_json_blocks(tmp_path, migrated_factory) -> None:
    await add_account(migrated_factory)
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
    html = (out / "index.html").read_text(encoding="utf-8")
    for block_id in ("data-json", "accounts-json", "downloads-json"):
        assert f'<script type="application/json" id="{block_id}">' in html
    # The embedded JSON blocks must actually parse.
    for block_id in ("data-json", "accounts-json", "downloads-json"):
        start = html.index(f'id="{block_id}">') + len(f'id="{block_id}">')
        end = html.index("</script>", start)
        json.loads(html[start:end])


# --- Column 2: accounts and consumption (T-DATA-10) ---------------------------


async def test_accounts_json_reads_total_disk_bytes_column_only(tmp_path, migrated_factory) -> None:
    """T-DATA-10: byte sizes come from the COLUMN; the filesystem is never walked."""
    account_id = await add_account(
        migrated_factory, total_disk_bytes=4321, mode="monitor", paused=True
    )
    await add_video(migrated_factory, account_id, "1", file_size=999)
    await add_video(migrated_factory, account_id, "2", file_size=999)
    await add_video(migrated_factory, account_id, "3", status="failed")
    # Junk that a filesystem walk WOULD pick up: must stay irrelevant.
    junk_dir = tmp_path / "downloads" / "acct"
    junk_dir.mkdir(parents=True)
    (junk_dir / "decoy.mp4").write_bytes(b"x" * 50_000)

    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)

    payload = read_json(out / "accounts.json")
    assert payload["approximation_note"]
    (row,) = payload["accounts"]
    assert row["username"] == "acct"
    assert row["mode"] == "monitor"
    assert row["paused"] is True
    assert row["videos_downloaded"] == 2
    assert row["total_disk_bytes"] == 4321  # the column value, EXACTLY
    assert "decoy" not in (out / "accounts.json").read_text(encoding="utf-8")


async def test_accounts_json_includes_backfill_progress(tmp_path, migrated_factory) -> None:
    await add_account(
        migrated_factory,
        username="bf",
        backfill_status="backfilling",
        backfill_done=7,
        backfill_total=20,
    )
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
    (row,) = read_json(out / "accounts.json")["accounts"]
    assert row["backfill_status"] == "backfilling"
    assert row["backfill_done"] == 7
    assert row["backfill_total"] == 20


# --- Column 3: downloads history ----------------------------------------------


async def test_downloads_json_respects_history_limit(tmp_path, migrated_factory) -> None:
    account_id = await add_account(migrated_factory, username="hist")
    for i in range(7):
        await add_video(
            migrated_factory,
            account_id,
            str(i),
            downloaded_at=f"2025-01-0{i + 1}T00:00:00+00:00",
        )
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)

    payload = read_json(out / "downloads.json")
    assert payload["history_limit"] == 5
    assert len(payload["downloads"]) == 5
    # Most recent first (shared _video_rows ordering).
    dates = [row["downloaded_at"] for row in payload["downloads"]]
    assert dates == sorted(dates, reverse=True)


async def test_downloads_json_counts_failures_by_category_over_full_history(
    tmp_path, migrated_factory
) -> None:
    """Failure counts are aggregates over ALL rows, not just the bounded window."""
    account_id = await add_account(migrated_factory)
    for i in range(3):
        await add_video(
            migrated_factory, account_id, f"f{i}", status="failed", error_category="definitive"
        )
    await add_video(migrated_factory, account_id, "t", status="failed", error_category="transient")
    await add_video(migrated_factory, account_id, "u", status="failed")  # NULL category
    # Push them all out of the 5-row window with newer downloads.
    for i in range(8):
        await add_video(
            migrated_factory,
            account_id,
            f"new{i}",
            downloaded_at=f"2025-02-0{(i % 9) + 1}T00:00:00+00:00",
        )
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)

    payload = read_json(out / "downloads.json")
    assert len(payload["downloads"]) == 5  # window still bounded
    assert payload["failures_by_category"] == {
        "definitive": 3,
        "transient": 1,
        "unknown": 1,
    }


# --- Column 1: summary via gather_status + clock -------------------------------


async def test_data_json_summary_from_gather_status_with_fake_clock(
    tmp_path, migrated_factory
) -> None:
    async with migrated_factory() as session:
        await register_daemon_start(session, pid=1234)
    await add_account(migrated_factory, mode="monitor")

    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)

    payload = read_json(out / "data.json")
    assert payload["generated_at"] == FAKE_NOW.isoformat()
    assert payload["summary"]["monitor_running"] is False
    assert payload["summary"]["daemon_pid"] == 1234


async def test_data_json_ytdlp_version_from_daemon_state_no_update_button(
    tmp_path, migrated_factory
) -> None:
    async with migrated_factory() as session:
        await register_daemon_start(session, pid=1)
        await record_last_known_good_ytdlp(session, "2025.05.22")

    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)

    payload = read_json(out / "data.json")
    assert payload["summary"]["ytdlp_version"] == "2025.05.22"
    # §8 newer-available detection is not tracked anywhere in the repo: omitted.
    assert "ytdlp_newer_available" not in payload["summary"]


async def test_data_json_absent_daemon_state_degrades_gracefully(
    tmp_path, migrated_factory
) -> None:
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
    payload = read_json(out / "data.json")
    assert payload["generated_at"] == FAKE_NOW.isoformat()
    assert "ytdlp_version" in payload["summary"]


# --- Column 4: command reference (injected, never imported) --------------------


async def test_command_reference_injected_appears_in_index(tmp_path, migrated_factory) -> None:
    reference = {
        "name": "tikdown-rs",
        "help": "root",
        "commands": [{"name": "daemon", "help": "lifecycle", "commands": []}],
    }
    out = tmp_path / "public"
    await render_site(
        migrated_factory,
        site_settings(tmp_path),
        out,
        command_reference=reference,
        now_fn=lambda: FAKE_NOW,
    )
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "tikdown-rs" in html
    assert "lifecycle" in html


async def test_command_reference_none_writes_placeholder(tmp_path, migrated_factory) -> None:
    out = tmp_path / "public"
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert NO_COMMAND_REFERENCE_PLACEHOLDER in html


# --- determinism / secrets / output dir ---------------------------------------


async def test_render_is_idempotent_with_fixed_clock(tmp_path, migrated_factory) -> None:
    await add_account(migrated_factory)
    out = tmp_path / "public"
    for _ in range(2):
        await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
        first = {name: (out / name).read_bytes() for name in OUTPUT_FILES}
    await render_site(migrated_factory, site_settings(tmp_path), out, now_fn=lambda: FAKE_NOW)
    for name in OUTPUT_FILES:
        assert (out / name).read_bytes() == first[name]


async def test_no_secrets_in_any_output_file(tmp_path, migrated_factory) -> None:
    """Cookies/tokens/env values NEVER leak into the dashboard (10.3)."""
    async with migrated_factory() as session:
        session.add(
            Cookie(label="leaky", cookie_blob=b"TOPSECRET-COOKIE-BLOB", validation_state="valid")
        )
        await session.commit()
    async with migrated_factory() as session:
        await register_daemon_start(session, pid=1)
    await add_account(migrated_factory)

    settings = site_settings(tmp_path, telegram_bot_token="TOPSECRET-TOKEN")
    out = tmp_path / "public"
    await render_site(migrated_factory, settings, out, now_fn=lambda: FAKE_NOW)
    for name in OUTPUT_FILES:
        content = (out / name).read_bytes()
        assert b"TOPSECRET" not in content, f"secret leaked into {name}"


async def test_resolve_output_dir_default_is_data_dir_public(tmp_path) -> None:
    settings = site_settings(tmp_path)
    assert resolve_output_dir(settings) == tmp_path / "public"
    custom = site_settings(tmp_path, static_site_dir=str(tmp_path / "elsewhere"))
    assert resolve_output_dir(custom) == tmp_path / "elsewhere"


# --- CLI: system site render ---------------------------------------------------


def test_cli_site_render_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATIC_SITE_DIR", str(tmp_path / "site"))
    runner = CliRunner()
    result = runner.invoke(app, ["system", "site", "render"])
    assert result.exit_code == 0, result.output
    for name in OUTPUT_FILES:
        assert (tmp_path / "site" / name).is_file()
    assert "index.html" in result.output
    assert result.output.isascii()


def test_cli_site_render_defaults_to_data_dir_public(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    result = runner.invoke(app, ["system", "site", "render"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "public" / "index.html").is_file()


# --- daemon scheduler job (conditional) ----------------------------------------


def _components(settings, factory):
    from tikdown_rs.core.notifications import InMemoryNotificationService
    from tikdown_rs.daemon.run import DaemonComponents

    return DaemonComponents(
        settings=settings,
        session_factory=factory,
        engine=None,
        pacer=None,
        semaphore=None,
        archive=None,
        network_monitor=None,
        network_available=asyncio.Event(),
        notifications=InMemoryNotificationService(),
        on_event=None,
    )


async def test_scheduler_includes_static_site_job_only_when_enabled(
    tmp_path, migrated_factory
) -> None:
    from tikdown_rs.daemon import run as daemon_run

    enabled = site_settings(tmp_path, static_site_enabled=True, static_site_interval_minutes=20)
    scheduler = daemon_run._build_scheduler(_components(enabled, migrated_factory))
    scheduler.start()
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "static-site" in job_ids
        intervals = {job.id: job.trigger.interval.total_seconds() for job in scheduler.get_jobs()}
        assert intervals["static-site"] == 20 * 60
    finally:
        scheduler.shutdown(wait=False)

    disabled = site_settings(tmp_path)
    scheduler2 = daemon_run._build_scheduler(_components(disabled, migrated_factory))
    scheduler2.start()
    try:
        assert "static-site" not in {job.id for job in scheduler2.get_jobs()}
    finally:
        scheduler2.shutdown(wait=False)


async def test_static_site_job_body_renders_files(tmp_path, migrated_factory) -> None:
    from tikdown_rs.daemon.run import _static_site_job

    await add_account(migrated_factory)
    settings = site_settings(tmp_path, static_site_enabled=True)
    await _static_site_job(_components(settings, migrated_factory))
    out = settings.data_dir / "public"
    for name in OUTPUT_FILES:
        assert (out / name).is_file()
