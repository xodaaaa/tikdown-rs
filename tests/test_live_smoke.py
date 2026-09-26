"""Live smoke for the download engine (M2, one-time manual run, §12.1/§13.1).

Trampas neutralizadas: T-DATA-2 (the engine must have worked against the real
target at least once, not only against fakes).
Regla: §13.1 tier 2 - explicit, separated, NEVER in the automatic gate.

Run manually exactly once per engine milestone change:
    uv run pytest tests/test_live_smoke.py -m live
"""

import asyncio
from typing import Any

import pytest

from tikdown_rs.core.config import Settings
from tikdown_rs.core.download_engine.ytdlp_engine import YtDlpEngine

# Empty-but-valid Netscape blob: the engine mandates cookies at construction
# (T-BACKFILL-12); a public feed listing does not need real cookies, only the
# impersonation + native WAF solver path (§2.2).
EMPTY_NETSCAPE = b"# Netscape HTTP Cookie File\n"

PUBLIC_ACCOUNT = "khaby.lame"  # verified public account with many videos.


@pytest.mark.live
async def test_list_public_feed_live() -> None:
    settings = Settings()
    engine = YtDlpEngine(cookies_blob=EMPTY_NETSCAPE, settings=settings)
    # Engine listing is sync (blocking yt-dlp): callers wrap in to_thread
    # (T-ASYNC-8). §14.6: 'Unable to extract secondary user ID' is a known
    # TRANSIENT degradation - a single failure here does not fail the milestone;
    # re-run manually or pick another verified public account.
    entries: list[dict[str, Any]] = await asyncio.to_thread(engine.list_videos, PUBLIC_ACCOUNT, 5)
    assert len(entries) >= 1, "public feed listing returned no entries"
    first = entries[0]
    assert first["id"], "entry without id"
    assert f"/video/{first['id']}" in first["url"], "URL not normalized to page URL"
