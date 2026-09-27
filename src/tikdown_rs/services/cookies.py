"""Cookie store services: add, list, remove, validate, get_working_cookie.

Trampas neutralizadas: T-COOKIES-2, T-COOKIES-3, T-COOKIES-4, T-COOKIES-8,
T-DB-15. Regla: 7, 3.4.

Services are pure (plan 1.1/12): no cli/, daemon/ or yt_dlp imports here.
The engine materialization (real YoutubeDLCookieJar) happens at the entry
point that needs cookies, never in this layer: the probe is INJECTED as
``probe_fn`` (T-DATA-3: classification via core.errors.classify_error only).
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.cookie_parser import to_canonical_netscape, warn_missing_sid_tt
from tikdown_rs.core.errors import ConfigurationError, classify_error
from tikdown_rs.models import Cookie

logger = logging.getLogger("tikdown_rs.services.cookies")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


async def add_cookie(
    session_factory: async_sessionmaker[AsyncSession],
    source_path: Path | str,
    label: str | None,
    keep_source: bool = False,
) -> int:
    """Import a cookies file: convert to canonical Netscape, persist, delete source.

    The blob is persisted immediately (7, 3.4) and the source file deletion is
    best-effort (T-COOKIES-8): a deletion failure logs a WARNING and the
    import still succeeds. A missing source file is a ConfigurationError.
    """
    source = Path(source_path)
    if not source.is_file():
        raise ConfigurationError(f"cookies source file not found: {source}")
    text = source.read_text(encoding="utf-8")
    warn_missing_sid_tt(text)
    blob = to_canonical_netscape(text).encode("utf-8")
    now = _utcnow_iso()

    async with session_factory() as session:
        row = Cookie(
            label=label,
            cookie_blob=blob,
            validation_state="inconclusive",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        cookie_id = row.id

    if not keep_source:
        try:
            source.unlink()
        except OSError as exc:
            logger.warning(
                "Could not delete cookies source file %s (%s); "
                "import succeeded anyway -- delete it manually or use --keep-source",
                source,
                exc,
            )
    return cookie_id


async def list_cookies(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[Cookie]:
    """All stored cookies with their validation lifecycle fields (3.4)."""
    from sqlalchemy import select

    async with session_factory() as session:
        result = await session.execute(select(Cookie).order_by(Cookie.id))
        return list(result.scalars().all())


async def remove_cookie(session_factory: async_sessionmaker[AsyncSession], cookie_id: int) -> None:
    """Delete a stored cookie; an unknown id is a ConfigurationError naming it."""
    async with session_factory() as session:
        row = await session.get(Cookie, cookie_id)
        if row is None:
            raise ConfigurationError(f"unknown cookie id: {cookie_id}")
        await session.delete(row)
        await session.commit()


async def validate_cookie(
    session_factory: async_sessionmaker[AsyncSession],
    cookie_id: int,
    probe_urls: list[str],
    probe_fn,
    max_entries: int,
) -> str:
    """Three-state cookie validation (7, T-COOKIES-3, T-DB-15).

    probe_urls are tried IN ORDER (fallback): a probe the operator verified is
    the evidence for a verdict, a degraded probe is not. Only an auth-CONFIRMED
    failure (classify_error -> 'definitive', 4.4 rule 2) is a verdict
    ('invalid'); every other failure path is 'transient' and falls through to
    the next candidate URL. When ALL candidates fail or yield no video entries
    (T-COOKIES-2: first entry may be an audio-only slideshow), the result is a
    GLOBAL 'inconclusive' and validation_state/last_validated_at are NOT
    touched (T-COOKIES-3: a broken probe never invalidates cookies).

    T-DB-15: the blob is read in a SHORT session that closes before the probe
    runs; persistence happens in a NEW short session afterwards. probe_fn is
    the injected sync probe (core.verify.probe_profile); the M2 engine wraps
    it with to_thread.
    """
    async with session_factory() as session:  # short session: read blob only
        row = await session.get(Cookie, cookie_id)
        if row is None:
            raise ConfigurationError(f"unknown cookie id: {cookie_id}")
        blob = row.cookie_blob

    verdict: str | None = None
    reason: str | None = None
    for url in probe_urls:
        try:
            # probe_fn returns True when the probe profile yielded video entries;
            # composing probe_profile + entries_have_video is the CALLER's job
            # (composition root) so this service never imports yt_dlp even
            # transitively (§4.8 layering intent, stricter than the arch test).
            # B1 (T-ASYNC-8): probe_fn composes blocking yt-dlp extraction —
            # never run it on the loop thread.
            has_video = await asyncio.to_thread(probe_fn, blob, url, max_entries)
        except Exception as exc:  # noqa: BLE001 - any failure is classified (7)
            if classify_error(exc) == "definitive":
                verdict = "invalid"  # auth confirmed against a verified probe
                reason = "authentication confirmed by probe"
                break
            continue  # transient: try the next candidate URL
        if has_video:
            verdict = "valid"
            reason = "probe profile returned video entries"
            break
        # No video entries across the first max_entries: next URL.

    if verdict is None:
        logger.warning(
            "cookie.validation_probe_failed: all %d probe candidates failed or "
            "returned no video entries; cookie %d left untouched (T-COOKIES-3)",
            len(probe_urls),
            cookie_id,
        )
        return "inconclusive"

    now = _utcnow_iso()
    async with session_factory() as session:  # NEW short session (T-DB-15)
        row = await session.get(Cookie, cookie_id)
        row.validation_state = verdict
        row.last_validated_at = now
        row.last_validation_reason = reason
        row.updated_at = now
        await session.commit()
    return verdict


async def get_working_cookie(
    session_factory: async_sessionmaker[AsyncSession],
) -> Cookie | None:
    """Best usable cookie: only 'invalid' is excluded (7, T-COOKIES-4).

    Candidates are 'valid' OR 'inconclusive' (an inconclusive state is logged
    informationally, never a rejection: a broken probe must not leave the
    system cookieless). 'valid' beats 'inconclusive' regardless of
    last_validated_at; within each group, last_validated_at DESC with NULLS
    LAST, so never-checked cookies remain candidates ordered last.

    ponytail: revalidation-on-stale is the 6h cookies-validate job (M4) and is
    intentionally NOT wired here.
    """
    valid_first = case((Cookie.validation_state == "valid", 0), else_=1)
    stmt = (
        select(Cookie)
        .where(Cookie.validation_state.in_(("valid", "inconclusive")))
        .order_by(valid_first, Cookie.last_validated_at.desc().nulls_last(), Cookie.id)
    )
    async with session_factory() as session:
        row = (await session.execute(stmt)).scalars().first()
    if row is not None and row.validation_state == "inconclusive":
        logger.info(
            "cookie %d selected with validation_state=inconclusive "
            "(informational; only 'invalid' is excluded, T-COOKIES-4)",
            row.id,
        )
    return row
