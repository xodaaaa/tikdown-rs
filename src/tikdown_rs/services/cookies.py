"""Cookie store services: add, list, remove.

Trampas neutralizadas: T-COOKIES-8. Regla: 7, 3.4.

Services are pure (plan 1.1/12): no cli/, daemon/ or yt_dlp imports here.
The engine materialization (real YoutubeDLCookieJar) happens at the entry
point that needs cookies, never in this layer.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.cookie_parser import to_canonical_netscape, warn_missing_sid_tt
from tikdown_rs.core.errors import ConfigurationError
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


async def remove_cookie(
    session_factory: async_sessionmaker[AsyncSession], cookie_id: int
) -> None:
    """Delete a stored cookie; an unknown id is a ConfigurationError naming it."""
    async with session_factory() as session:
        row = await session.get(Cookie, cookie_id)
        if row is None:
            raise ConfigurationError(f"unknown cookie id: {cookie_id}")
        await session.delete(row)
        await session.commit()
