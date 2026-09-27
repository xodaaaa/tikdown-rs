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
import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.notifications.events import (
    EVENT_COOKIE_VALIDATED,
    EVENT_COOKIE_VALIDATION_PROBE_FAILED,
    EVENT_PROFILE_REFRESHED,
)
from tikdown_rs.models import Cookie, MonitoredAccount
from tikdown_rs.services.cookies import validate_cookie

logger = logging.getLogger("tikdown_rs.services.maintenance")

#: 7/11.2: 30-60 s between probes, drawn so the cadence is not fixed.
_COOKIE_VALIDATION_SLEEP_RANGE = (30.0, 60.0)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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
            profile = engine.extract_profile(username)
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
            account.following_count = profile.get("following_count")
            account.total_likes = profile.get("total_likes")
            account.video_count = profile.get("video_count")
            account.profile_last_refreshed = _utcnow_iso()
            await session.commit()
        refreshed += 1
        _emit_event(on_event, EVENT_PROFILE_REFRESHED, account_id=account_id, username=username)
    return refreshed
