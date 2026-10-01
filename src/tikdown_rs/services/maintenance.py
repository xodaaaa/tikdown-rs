"""Maintenance services: periodic cookie validation + profile refresh.

Trampas neutralizadas: T-DB-15, T-COOKIES-3. Regla: 7, 3.1, 5.3, 11.2.

``validate_all_cookies`` orchestrates the M1 three-state validation
(``services.cookies.validate_cookie``) SEQUENTIALLY over every candidate
cookie row, sleeping 30-60 s BETWEEN probes via the injected ``sleep_fn`` --
never inside a session (T-DB-15: short sessions only). The M1 function owns
verdict persistence and its T-COOKIES-3 log; this layer adds the catalog
events (6.2): ``cookie.validated`` per verdict and
``cookie.validation_probe_failed`` on a global inconclusive.

``refresh_all_profiles`` runs the 48 h profile refresh (5.3, 11.2): one
``extract_profile`` per non-paused account, counters persisted in a short
session; a failing account is logged and skipped, never blocking the others.

Layering (4.8): nothing here imports cli/, daemon/ or yt_dlp; the engine and
the probe arrive INJECTED and the event channel is SYNC (T-BACKFILL-15).
"""

import asyncio
import logging
import os
import random
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.notifications.events import (
    EVENT_COOKIE_VALIDATED,
    EVENT_COOKIE_VALIDATION_PROBE_FAILED,
    EVENT_PROFILE_REFRESHED,
)
from tikdown_rs.core.timeutil import utcnow_iso as _utcnow_iso
from tikdown_rs.models import Cookie, MonitoredAccount
from tikdown_rs.services.cookies import validate_cookie

logger = logging.getLogger("tikdown_rs.services.maintenance")

#: 7/11.2: 30-60 s between probes, drawn so the cadence is not fixed.
_COOKIE_VALIDATION_SLEEP_RANGE = (30.0, 60.0)


def _emit_event(on_event, event: str, **fields) -> None:
    """Fire the SYNC channel with a catalog event name (6.2, T-BACKFILL-15)."""
    if on_event is None:
        return
    on_event({"event": event, **fields})


async def validate_all_cookies(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    probe_urls: list[str],
    probe_fn,
    max_entries: int,
    on_event=None,
    sleep_fn=None,
) -> dict:
    """Sequentially validate every candidate cookie row (7, T-DB-15).

    Candidates are rows whose ``validation_state`` is 'valid' or
    'inconclusive' -- i.e. everything EXCEPT 'invalid' (never-validated rows
    default to 'inconclusive', so they are included; an 'invalid' row already
    carries an auth-confirmed verdict and is never re-probed). Verdict
    persistence and the T-COOKIES-3 log live in
    ``services.cookies.validate_cookie``; this layer emits ``cookie.validated``
    per verdict plus ``cookie.validation_probe_failed`` on a global
    inconclusive.

    The injected ``sleep_fn`` (default ``asyncio.sleep``) runs BETWEEN probes
    -- ``probes - 1`` times, 30-60 s per 7/11.2 -- and NEVER inside a session
    (T-DB-15). Returns counts ``{valid, invalid, inconclusive}``.
    """
    sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
    async with session_factory() as session:  # short session: ids only
        rows = (
            (
                await session.execute(
                    select(Cookie.id)
                    .where(Cookie.validation_state.in_(("valid", "inconclusive")))
                    .order_by(Cookie.id)
                )
            )
            .scalars()
            .all()
        )
        cookie_ids = list(rows)

    counts = {"valid": 0, "invalid": 0, "inconclusive": 0}
    for index, cookie_id in enumerate(cookie_ids):
        verdict = await validate_cookie(
            session_factory, cookie_id, probe_urls, probe_fn, max_entries
        )
        counts[verdict] += 1
        _emit_event(on_event, EVENT_COOKIE_VALIDATED, cookie_id=cookie_id, state=verdict)
        if verdict == "inconclusive":
            # The M1 probe-failure log already happened; the event makes the
            # global inconclusive VISIBLE (6.2) without touching the row
            # (T-COOKIES-3: a broken probe never invalidates cookies).
            _emit_event(
                on_event,
                EVENT_COOKIE_VALIDATION_PROBE_FAILED,
                cookie_id=cookie_id,
                count=len(probe_urls),
            )
        if index < len(cookie_ids) - 1:
            await sleep(random.uniform(*_COOKIE_VALIDATION_SLEEP_RANGE))
    return counts


async def refresh_all_profiles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    engine,
    on_event=None,
) -> int:
    """Refresh profile counters for every non-paused account (5.3, 3.1).

    Any mode qualifies (history accounts keep counters too); a paused account
    is skipped. Per-account isolation: a transient ``extract_profile`` failure
    is logged and skipped, never blocking the others (default rule 10: a
    profile failure is transient, it never pauses or flags the account).
    Emits ``profile.refreshed`` per refreshed account; returns the count.
    """
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MonitoredAccount.id, MonitoredAccount.username)
                .where(MonitoredAccount.paused.is_(False))
                .order_by(MonitoredAccount.id)
            )
        ).all()
        targets = [(row.id, row.username) for row in rows]

    refreshed = 0
    for account_id, username in targets:
        try:
            # B1 (T-ASYNC-8): blocking yt-dlp extraction — never on the loop.
            profile = await asyncio.to_thread(engine.extract_profile, username)
        except Exception as exc:  # noqa: BLE001 - isolation: skip and continue
            logger.warning(
                "profile.refresh skipped for %s (%s): %s",
                username,
                "transient",
                exc,
            )
            continue
        async with session_factory() as session:  # short session (T-DB-15)
            account = await session.get(MonitoredAccount, account_id)
            account.follower_count = profile.get("followers")
            account.video_count = profile.get("video_count")
            account.profile_last_refreshed = _utcnow_iso()
            await session.commit()
        refreshed += 1
        _emit_event(on_event, EVENT_PROFILE_REFRESHED, account_id=account_id, username=username)
    return refreshed


# --- create_backup (14.5: VACUUM INTO snapshot + retention, 15.2 rule 2) ---

#: 14.5 snapshot name: one file per invocation, UTC-second resolution.
_SNAPSHOT_RE = re.compile(r"^tikdown-rs-\d{8}T\d{6}Z\.db$")


def _apply_retention(directory: Path, retain: int) -> int:
    """Delete the oldest snapshots beyond ``retain``; return how many.

    ONLY files matching the snapshot naming pattern are candidates -- an
    operator's unrelated files in <DATA_DIR>/backups are never touched.
    ``retain == 0`` deletes every snapshot (the field is ge=0 by design).
    """
    snapshots = sorted(p for p in directory.iterdir() if _SNAPSHOT_RE.match(p.name))
    victims = snapshots if retain == 0 else snapshots[:-retain]
    for path in victims:
        path.unlink()
    return len(victims)


async def create_backup(
    session_factory: async_sessionmaker[AsyncSession],
    settings,
    *,
    now_fn=None,
) -> tuple[Path, int]:
    """Snapshot the live database with SQLite ``VACUUM INTO`` (14.5).

    Why VACUUM INTO and never a file copy: the database runs in WAL mode
    (db.py PRAGMA_STATEMENTS); copying ``tikdown-rs.db`` while ``-wal`` still
    holds committed frames yields an inconsistent or corrupt copy. VACUUM INTO
    is the ONLINE backup for a live WAL database: it reads a consistent
    committed snapshot through the connection (busy_timeout applies) while
    writers keep running, and produces one self-contained compacted file. It
    therefore works with the daemon running AND from the CLI while stopped.

    The database contains unencrypted cookies, so the snapshot is a SECRET
    (15.2 rule 2): it gets ``chmod 0600``, best-effort -- on Windows chmod
    only toggles the read-only flag, so a PermissionError is logged and
    swallowed instead of failing an otherwise complete backup.

    Same-second invocations collide on the timestamped name: the new snapshot
    is staged as ``*.db.tmp`` (a name that never matches the retention
    pattern) and published with ``os.replace`` (atomic). A collision
    deterministically overwrites WITHOUT ever unlinking the previous snapshot
    before its replacement is complete (R3-001/R4-001), and retention only
    ever sees complete final-name snapshots (R4-002).

    Retention keeps the newest ``settings.system_backup_retain_count`` files
    matching the snapshot pattern; nothing else in the directory is deleted.
    Returns ``(snapshot_path, deleted_count)``. The snapshot is NOT opened or
    verified beyond being written -- restore-time validation is
    ``daemon selfcheck`` (14.5).
    """
    now = (now_fn if now_fn is not None else lambda: datetime.now(UTC))()
    directory = settings.data_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"tikdown-rs-{now:%Y%m%dT%H%M%S}Z.db"
    # R3-001/R4-001: stage the VACUUM output and publish atomically. The
    # final-name file is only ever replaced by a COMPLETE new snapshot, so a
    # failed or interrupted run can never destroy the last known-good
    # snapshot, and a same-second collision overwrites without an
    # unlink-first window.
    tmp = target.with_name(target.name + ".tmp")  # never matches _SNAPSHOT_RE
    tmp.unlink(missing_ok=True)  # leftover from a previous crashed run
    try:
        async with session_factory() as session:  # short session (T-DB-15)
            # VACUUM cannot run inside a transaction: AUTOCOMMIT from the start
            # (before autobegin) keeps the implicit BEGIN out of the way.
            connection = await session.connection(
                execution_options={"isolation_level": "AUTOCOMMIT"}
            )
            await connection.execute(text("VACUUM INTO :path"), {"path": str(tmp)})
        try:
            os.chmod(tmp, 0o600)  # 15.2 rule 2; best-effort on Windows
        except PermissionError:
            logger.info("backup chmod 0600 skipped (not permitted): %s", tmp)
        os.replace(tmp, target)  # atomic publish (R3-001/R4-001)
    except BaseException:
        tmp.unlink(missing_ok=True)  # clean the stage; previous snapshots intact
        raise
    deleted = _apply_retention(directory, settings.system_backup_retain_count)
    return target, deleted
