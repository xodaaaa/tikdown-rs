"""Download engine package (4.8, 12 structure: core/download_engine).

Trampas neutralizadas: T-ENGINE-16 (contract test over the concrete class),
T-ENGINE-17 (unambiguous naming). Regla: 4.8.
"""

from tikdown_rs.core.download_engine.protocol import (
    DownloadEngine,
    ProfileData,
    VideoData,
)
from tikdown_rs.core.download_engine.ytdlp_engine import YtDlpEngine

__all__ = [
    "DownloadEngine",
    "ProfileData",
    "VideoData",
    "YtDlpEngine",
]
