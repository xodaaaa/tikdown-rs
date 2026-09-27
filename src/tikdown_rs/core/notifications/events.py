"""Notification event catalog and renderer (6.2, T-DATA-9, T-BOT-13).

The BASE scope of notifications (6.2) is exactly this module: the catalog of
event templates plus a synchronous ``render`` function. The persistent spool
and the real send are OUT of scope (17.1): this catalog is the contract that
makes the future push epic cheap, because having it half-done costs more than
not having it.

Trampas: T-DATA-9 (every catalog event needs a REAL producer; the parity test
scans src/ EXCLUDING this file -- without that exclusion the assertion is
vacuous, T-BOT-13), T-BOT-10 (templates already carry the '@'; render never
adds it), T-BOT-6 (4096-char message semantics; ``clip()`` arrives with the
bot in M5).

Template rules:
- ASCII only; dotted lowercase event names; ``{placeholder}`` style.
- NO format specs (``{n:.0f}``): missing-key tolerance inserts a placeholder
  STRING and a format spec would raise on it. Job paths must never crash on
  notification rendering (6.2).
- ``deferred`` marks an event whose producer is NOT wired in src/ yet: the
  milestone string where its producer lands ('M4' = the M4 daemon jobs,
  'M5' = the bot producers). Removal is a ONE-WORD edit: set ``deferred=None``
  once the producer exists -- the parity test then enforces its presence
  (T-DATA-9).
"""

from dataclasses import dataclass

from tikdown_rs.core.errors import ConfigurationError

# --- event names: the ONLY literals; producers import these constants ---

EVENT_DAEMON_STARTED = "daemon.started"
EVENT_DAEMON_STOPPED = "daemon.stopped"
EVENT_DAEMON_DB_CONTENTION = "daemon.db_contention"
EVENT_MONITOR_STARTED = "monitor.started"
EVENT_MONITOR_STOPPED = "monitor.stopped"
EVENT_MONITOR_STOPPED_NO_COOKIES = "monitor.stopped_no_cookies"
EVENT_MONITOR_VIDEO_DISCOVERED = "monitor.video_discovered"
EVENT_DOWNLOAD_DOWNLOADED = "download.downloaded"
EVENT_DOWNLOAD_SKIPPED = "download.skipped"
EVENT_DOWNLOAD_FAILED = "download.failed"
EVENT_BACKFILL_STARTED = "backfill.started"
EVENT_BACKFILL_COMPLETED = "backfill.completed"
EVENT_BACKFILL_CANCELLED = "backfill.cancelled"
EVENT_BACKFILL_PAUSED = "backfill.paused"
EVENT_BACKFILL_NO_COOKIES = "backfill.no_cookies"
EVENT_COOKIE_VALIDATION_PROBE_FAILED = "cookie.validation_probe_failed"
EVENT_COOKIE_VALIDATED = "cookie.validated"
EVENT_NETWORK_OFFLINE = "network.offline"
EVENT_NETWORK_ONLINE = "network.online"
EVENT_DISK_WARNING = "disk.warning"
EVENT_DISK_PAUSED = "disk.paused"
EVENT_DISK_RESUMED = "disk.resumed"
EVENT_SELFCHECK_FAILED = "selfcheck.failed"
EVENT_SELFCHECK_OK = "selfcheck.ok"
EVENT_PROFILE_REFRESHED = "profile.refreshed"
EVENT_BOT_UNAUTHORIZED_ATTEMPT = "bot.unauthorized_attempt"
EVENT_BOT_UNAUTHORIZED_ATTEMPTS_BURST = "bot.unauthorized_attempts_burst"
EVENT_BOT_STARTED = "bot.started"
EVENT_BOT_STOPPED = "bot.stopped"


@dataclass(frozen=True)
class EventSpec:
    """One catalog event (6.2): name, template, description, deferral."""

    name: str
    template: str
    description: str
    #: None once a real producer exists; else the milestone that wires it.
    deferred: str | None = None


EVENTS: dict[str, EventSpec] = {
    EVENT_DAEMON_STARTED: EventSpec(
        EVENT_DAEMON_STARTED,
        "Daemon started (pid {pid}, data_dir {data_dir}).",
        "The daemon finished startup (5.1).",
        deferred=None,
    ),
    EVENT_DAEMON_STOPPED: EventSpec(
        EVENT_DAEMON_STOPPED,
        "Daemon stopped ({reason}).",
        "Graceful shutdown; emitted BEFORE the drain (5.2, T-ASYNC-6).",
        deferred=None,
    ),
    EVENT_DAEMON_DB_CONTENTION: EventSpec(
        EVENT_DAEMON_DB_CONTENTION,
        "SQLite contention: {count} 'database is locked' errors in the last 5 min.",
        "Deduped per edge by the heartbeat (5.6).",
        deferred=None,
    ),
    EVENT_MONITOR_STARTED: EventSpec(
        EVENT_MONITOR_STARTED,
        "Monitor started.",
        "The monitor loop became active (3.3).",
        deferred=None,
    ),
    EVENT_MONITOR_STOPPED: EventSpec(
        EVENT_MONITOR_STOPPED,
        "Monitor stopped.",
        "The monitor loop stopped (3.3).",
        deferred=None,
    ),
    EVENT_MONITOR_STOPPED_NO_COOKIES: EventSpec(
        EVENT_MONITOR_STOPPED_NO_COOKIES,
        "Monitor stopped: no valid cookies left.",
        "Global auto-stop when the last valid cookie expires (5.8).",
        deferred=None,
    ),
    EVENT_MONITOR_VIDEO_DISCOVERED: EventSpec(
        EVENT_MONITOR_VIDEO_DISCOVERED,
        "New video from @{username}: {title} ({url}).",
        "A monitored account published a new video.",
        deferred=None,
    ),
    EVENT_DOWNLOAD_DOWNLOADED: EventSpec(
        EVENT_DOWNLOAD_DOWNLOADED,
        "Downloaded {video_id}: {file_path}.",
        "A video was downloaded and verified (4.7).",
    ),
    EVENT_DOWNLOAD_SKIPPED: EventSpec(
        EVENT_DOWNLOAD_SKIPPED,
        "Skipped {video_id}: no video stream (slideshow).",
        "An expected slideshow was skipped without retries (T-ENGINE-5).",
    ),
    EVENT_DOWNLOAD_FAILED: EventSpec(
        EVENT_DOWNLOAD_FAILED,
        "Download failed for {video_id} ({error_category}).",
        "A download reached terminal failure (3.3).",
    ),
    EVENT_BACKFILL_STARTED: EventSpec(
        EVENT_BACKFILL_STARTED,
        "Backfill started for @{username}.",
        "A backfill run acquired the slot and entered 'backfilling' (9.1).",
    ),
    EVENT_BACKFILL_COMPLETED: EventSpec(
        EVENT_BACKFILL_COMPLETED,
        "Backfill completed for @{username}: {done}/{total} videos.",
        "A backfill run reached 'completed' (9.2).",
    ),
    EVENT_BACKFILL_CANCELLED: EventSpec(
        EVENT_BACKFILL_CANCELLED,
        "Backfill cancelled for @{username}.",
        "A cooperative cancel won before completion (9.4).",
    ),
    EVENT_BACKFILL_PAUSED: EventSpec(
        EVENT_BACKFILL_PAUSED,
        "Backfill paused for @{username} ({reason}); it resumes automatically.",
        "Paused with an identified cause: disk or network (9.1, T-BACKFILL-6).",
    ),
    EVENT_BACKFILL_NO_COOKIES: EventSpec(
        EVENT_BACKFILL_NO_COOKIES,
        "Backfill aborted for @{username}: no valid cookies.",
        "The run aborted at the mandatory cookie gate (9.1).",
    ),
    EVENT_COOKIE_VALIDATION_PROBE_FAILED: EventSpec(
        EVENT_COOKIE_VALIDATION_PROBE_FAILED,
        "Cookie validation probe failed for all {count} candidates.",
        "Every probe candidate failed; global 'inconclusive' (7, T-COOKIES-3).",
        deferred=None,
    ),
    EVENT_COOKIE_VALIDATED: EventSpec(
        EVENT_COOKIE_VALIDATED,
        "Cookie for @{username} is {state}.",
        "A validation cycle reached a verdict for a cookie (7).",
        deferred=None,
    ),
    EVENT_NETWORK_OFFLINE: EventSpec(
        EVENT_NETWORK_OFFLINE,
        "Network offline: {failures} consecutive probe failures.",
        "Offline CONFIRMED after the threshold (8.1).",
        deferred=None,
    ),
    EVENT_NETWORK_ONLINE: EventSpec(
        EVENT_NETWORK_ONLINE,
        "Network back online after {seconds} s.",
        "Only after confirmed offline, never on a blip (8.1, T-BOT-14).",
        deferred=None,
    ),
    EVENT_DISK_WARNING: EventSpec(
        EVENT_DISK_WARNING,
        "Disk space low on {path}: {free_percent}% free.",
        "Periodic check under the warning threshold (8.2).",
        deferred=None,
    ),
    EVENT_DISK_PAUSED: EventSpec(
        EVENT_DISK_PAUSED,
        "Downloads paused: disk almost full ({free_percent}% free).",
        "ENOSPC handling: local actionable pause (8.2, T-ENGINE-27).",
        deferred=None,
    ),
    EVENT_DISK_RESUMED: EventSpec(
        EVENT_DISK_RESUMED,
        "Downloads resumed: disk space recovered ({free_percent}% free).",
        "Automatic resume above the threshold (8.2).",
        deferred=None,
    ),
    EVENT_SELFCHECK_FAILED: EventSpec(
        EVENT_SELFCHECK_FAILED,
        "Selfcheck failed: {reason}.",
        "The periodic selfcheck detected a regression (5.3).",
        deferred=None,
    ),
    EVENT_SELFCHECK_OK: EventSpec(
        EVENT_SELFCHECK_OK,
        "Selfcheck OK.",
        "The periodic selfcheck passed (5.3).",
        deferred=None,
    ),
    EVENT_PROFILE_REFRESHED: EventSpec(
        EVENT_PROFILE_REFRESHED,
        "Profile counters refreshed for @{username}.",
        "The 48 h profile-refresh job ran for the account (5.3).",
        deferred=None,
    ),
    EVENT_BOT_UNAUTHORIZED_ATTEMPT: EventSpec(
        EVENT_BOT_UNAUTHORIZED_ATTEMPT,
        "Unauthorized bot attempt: user {user_id} in chat {chat_id}.",
        "A double-layer authorization rejection (6.3).",
        deferred="M5",
    ),
    EVENT_BOT_UNAUTHORIZED_ATTEMPTS_BURST: EventSpec(
        EVENT_BOT_UNAUTHORIZED_ATTEMPTS_BURST,
        "Burst of unauthorized bot attempts from user {user_id}: {count} in 5 min.",
        "Burst detection for repeated unauthorized attempts (6.3).",
        deferred="M5",
    ),
    EVENT_BOT_STARTED: EventSpec(
        EVENT_BOT_STARTED,
        "Bot started.",
        "The Telegram bot entered polling (6.1).",
        deferred="M5",
    ),
    EVENT_BOT_STOPPED: EventSpec(
        EVENT_BOT_STOPPED,
        "Bot stopped.",
        "The Telegram bot shut down cleanly (6.1).",
        deferred="M5",
    ),
}


class _SafeDict(dict):
    """Missing payload keys render as ``{key}`` placeholders, never raise (6.2)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(event: str, payload: dict) -> str:
    """Render one catalog event; never raises on payload gaps (6.2).

    An unknown event is a ConfigurationError: emitting an event without a
    template would lose it in silence (T-BOT-13). Templates must not carry
    format specs -- the missing-key tolerance inserts a string placeholder
    and a spec would raise on it.
    """
    spec = EVENTS.get(event)
    if spec is None:
        raise ConfigurationError(f"unknown notification event: {event}")
    return spec.template.format_map(_SafeDict(payload))
