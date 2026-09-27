"""Static dashboard site: the M6 T6 renderer (§10.3).

Regla: 10.3 with the §0.4 admission rule applied hard: this module writes
FOUR files (`index.html`, `accounts.json`, `downloads.json`, `data.json`)
and NOTHING else. No server, no port, no endpoint — the embedded JS only
filters/sorts/searches over the JSON already embedded in `index.html`.

Data sources (admission rule: only what is already computed at render time):
- Column 1 (Resumen general): REUSES `services/status.py::gather_status`
  (same snapshot as `daemon status` / the bot's /status) plus the
  yt-dlp version tracked in `daemon_state.last_known_good_ytdlp_version`
  (T-ENGINE-28). A "newer yt-dlp available" flag is NOT tracked anywhere in
  the repo, so it is omitted, never fetched (§0.4).
- Column 2 (Cuentas y consumo): read-only `monitored_accounts` columns.
  T-DATA-10: `total_disk_bytes` is READ from the column, NEVER recomputed
  from the filesystem. Labeled as an approximation of consumed network
  traffic (excludes failed retries and feed-listing traffic).
- Column 3 (Historial de descargas): the shared T2/T3 `_video_rows` query
  bounded by `settings.static_site_history_limit`, plus failure counts
  grouped by `error_category` aggregated over ALL history.
- Column 4 (Documentacion): the command reference arrives as a PARAMETER
  (plain JSON-safe data). services/* must not import cli/ (13.2), so the
  typer introspection happens at the call site (cli/system.py); callers
  without a CLI tree (the daemon scheduler job) pass None and get a
  placeholder.

Secrets (10.3): no cookies, tokens or .env values are ever read into the
payloads — only the aggregate/validation state, never `cookie_blob`.

Determinism: `now_fn` injects the generation clock (tests); with a fixed
clock and identical data every rendered byte is identical. Writes are
atomic-enough for a job: write-to-temp then `os.replace`.
"""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tikdown_rs.core.config import Settings
from tikdown_rs.models import DaemonState, MonitoredAccount, Video
from tikdown_rs.services.status import gather_status
from tikdown_rs.services.videos import _video_rows

#: 10.3: an unbounded history inflates every regeneration; the limit comes
#: from settings (default 500). Failure counts below are NOT bounded: they
#: are per-category aggregates, not rows.
HISTORY_LIMIT_FALLBACK = 500

#: 10.3 Column 2 wording: the per-account byte total is an approximation.
APPROXIMATION_NOTE = (
    "total_disk_bytes por cuenta es una APROXIMACION del trafico de red "
    "consumido: no incluye reintentos fallidos ni el trafico de listar el feed."
)

#: 10.3 Column 4: shown when no CLI tree was injected (scheduler job).
NO_COMMAND_REFERENCE_PLACEHOLDER = (
    "command reference unavailable (rendered without CLI introspection)"
)

#: 10.3: short "what is this project" blurb (user-facing copy follows the
#: bot's Spanish wording, 6.3).
ABOUT_BLURB = (
    "TikDown-rs es un daemon auto-alojado que archiva videos de cuentas de "
    "TikTok: descarga con yt-dlp, valida integridad y guarda todo en tu disco. "
    "Este panel es un conjunto de archivos estaticos: se regenera "
    "periodicamente y no expone ningun servidor. Se opera por CLI y por el "
    "bot de Telegram; aqui solo hay lectura."
)


def resolve_output_dir(settings: Settings) -> Path:
    """Settings sanity (11.1): empty STATIC_SITE_DIR defaults to <data_dir>/public."""
    if settings.static_site_dir:
        return Path(settings.static_site_dir)
    return settings.data_dir / "public"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- column gatherers (all read-only) ------------------------------------------


async def _summary(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> dict[str, Any]:
    """Column 1: gather_status reuse + the tracked yt-dlp version (T-ENGINE-28)."""
    async with session_factory() as session:
        status = await gather_status(session, data_dir=str(settings.data_dir), now=now)
        last_good = (
            await session.execute(select(DaemonState.last_known_good_ytdlp_version))
        ).scalar_one_or_none()
    if status is None:
        # The daemon never ran: degrade gracefully, keep the site renderable.
        return {
            "daemon_state": "absent (the daemon never ran)",
            "ytdlp_version": last_good,
        }
    data = status.to_dict()
    # No "newer available" flag: §8 detection is not implemented/tracked here,
    # and the admission rule forbids fetching it at render time.
    data["ytdlp_version"] = last_good
    return data


async def _accounts(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, Any]]:
    """Column 2: per-account read-only projection; total_disk_bytes is THE size."""
    async with session_factory() as session:
        accounts = (
            (await session.execute(select(MonitoredAccount).order_by(MonitoredAccount.username)))
            .scalars()
            .all()
        )
        downloaded_counts = dict(
            (
                await session.execute(
                    select(Video.account_id, func.count())
                    .where(Video.status == "downloaded")
                    .group_by(Video.account_id)
                )
            ).all()
        )
    return [
        {
            "username": account.username,
            "mode": account.mode,
            "paused": bool(account.paused),
            "needs_review": bool(account.needs_review),
            "videos_downloaded": downloaded_counts.get(account.id, 0),
            "total_disk_bytes": account.total_disk_bytes,  # T-DATA-10: read-only
            "backfill_status": account.backfill_status,
            "backfill_done": account.backfill_done,
            "backfill_total": account.backfill_total,
        }
        for account in accounts
    ]


async def _downloads(
    session_factory: async_sessionmaker[AsyncSession], history_limit: int
) -> dict[str, Any]:
    """Column 3: bounded shared _video_rows query + whole-history failure counts."""
    rows = await _video_rows(session_factory, limit=history_limit, exclude_pending=True)
    async with session_factory() as session:
        failures = dict(
            (
                await session.execute(
                    select(Video.error_category, func.count())
                    .where(Video.status == "failed")
                    .group_by(Video.error_category)
                )
            ).all()
        )
    return {
        "history_limit": history_limit,
        "downloads": rows,
        "failures_by_category": {
            category or "unknown": count for category, count in failures.items()
        },
    }


# --- serialization / writing ----------------------------------------------------


def _dump(payload: Any) -> str:
    """Deterministic JSON: sorted keys; `</` escaped so embedding in <script> is safe."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).replace("</", "<\\/")


def _write_atomic(path: Path, content: str) -> None:
    """Write-then-replace: a job crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikDown-rs - Panel de estadisticas</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 1rem; background: #f4f4f4; color: #1a1a1a; }
  h1 { font-size: 1.3rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }
  .card { background: #fff; border: 1px solid #ccc; border-radius: 6px; padding: 0.75rem; overflow-x: auto; }
  .card h2 { font-size: 1rem; margin: 0 0 0.5rem; }
  input[type="search"] { width: 100%; box-sizing: border-box; margin-bottom: 0.5rem; padding: 0.3rem; }
  table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
  th, td { border: 1px solid #ddd; padding: 0.25rem 0.4rem; text-align: left; }
  th { cursor: pointer; background: #eee; white-space: nowrap; }
  th.sorted::after { content: " \\25BE"; }
  dl { font-size: 0.85rem; } dt { font-weight: bold; margin-top: 0.4rem; } dd { margin: 0 0 0 0.8rem; word-break: break-all; }
  ul.cmdref { list-style: none; padding-left: 0.8rem; font-size: 0.85rem; }
  ul.cmdref > li::before { content: "> "; color: #888; }
  .hint { font-size: 0.8rem; color: #666; }
  @media (prefers-color-scheme: dark) {
    body { background: #1a1a1a; color: #e6e6e6; }
    .card { background: #242424; border-color: #444; }
    th { background: #333; } th, td { border-color: #444; }
    .hint { color: #999; }
  }
</style>
</head>
<body>
<h1>TikDown-rs - Panel de estadisticas</h1>
<p class="hint">Archivos estaticos: el filtrado y orden se hacen en tu navegador; nada de este panel controla el daemon.</p>
<div class="grid">
  <section class="card" id="col-resumen"><h2>Resumen general</h2><dl id="summary"></dl></section>
  <section class="card" id="col-cuentas"><h2>Cuentas y consumo</h2><p class="hint" id="approx-note"></p><div id="accounts"></div></section>
  <section class="card" id="col-historial"><h2>Historial de descargas</h2><div id="downloads"></div><h2>Fallos por categoria</h2><dl id="failures"></dl></section>
  <section class="card" id="col-docs"><h2>Documentacion</h2><p id="about"></p><div id="cmdref"></div></section>
</div>
<script type="application/json" id="data-json">__DATA_JSON__</script>
<script type="application/json" id="accounts-json">__ACCOUNTS_JSON__</script>
<script type="application/json" id="downloads-json">__DOWNLOADS_JSON__</script>
<script type="application/json" id="docs-json">__DOCS_JSON__</script>
<script>
"use strict";
function readJSON(id) { return JSON.parse(document.getElementById(id).textContent); }
function el(tag, text) { const e = document.createElement(tag); if (text != null) e.textContent = text; return e; }
function fmtBytes(n) { if (n == null) return "-"; const u = ["B", "KB", "MB", "GB", "TB"]; let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; } return (i ? v.toFixed(1) : v) + " " + u[i]; }
function renderSummary(data) {
  const dl = document.getElementById("summary");
  const rows = Object.entries(data.summary || {});
  const shown = rows.filter(([k]) => k !== "recent_errors");
  shown.forEach(([k, v]) => { dl.appendChild(el("dt", k));
    dl.appendChild(el("dd", Array.isArray(v) ? v.join("; ") : (v == null ? "-" : String(v)))); });
  const errs = (data.summary || {}).recent_errors;
  if (errs && errs.length) { dl.appendChild(el("dt", "recent_errors"));
    errs.forEach(line => dl.appendChild(el("dd", line))); }
}
function makeTable(mountId, rows, cols) {
  const mount = document.getElementById(mountId);
  const input = document.createElement("input");
  input.type = "search"; input.placeholder = "Buscar..."; input.setAttribute("aria-label", "Buscar");
  const table = el("table"); const thead = el("thead"); const trh = el("tr"); const tbody = el("tbody");
  let view = rows.slice(), sortKey = null, sortDir = 1;
  cols.forEach(c => {
    const th = el("th", c.label);
    th.onclick = () => {
      if (sortKey === c.key) { sortDir = -sortDir; } else { sortKey = c.key; sortDir = 1; }
      view = view.slice().sort((a, b) => {
        const x = a[c.key], y = b[c.key];
        return (x == null ? -1 : y == null ? 1 : x == y ? 0 : x > y ? 1 : -1) * sortDir; });
      Array.from(trh.children).forEach(t => t.classList.remove("sorted"));
      th.classList.add("sorted"); renderBody(); };
    trh.appendChild(th); });
  thead.appendChild(trh);
  function renderBody() {
    tbody.textContent = "";
    const q = input.value.trim().toLowerCase();
    view.filter(r => !q || cols.some(c => String(r[c.key] == null ? "" : r[c.key]).toLowerCase().includes(q)))
      .forEach(r => { const tr = el("tr");
        cols.forEach(c => { const v = r[c.key]; tr.appendChild(el("td", c.fmt ? c.fmt(v, r) : (v == null ? "-" : String(v)))); });
        tbody.appendChild(tr); }); }
  input.oninput = renderBody;
  table.appendChild(thead); table.appendChild(tbody);
  mount.appendChild(input); mount.appendChild(table);
  renderBody();
}
function renderCommandReference(node, mount, depth) {
  if (!node) { return; }
  if (depth > 0) {
    const item = el("li");
    item.appendChild(el("strong", node.name || "-"));
    if (node.help) item.appendChild(document.createTextNode(" - " + node.help));
    const nested = el("ul"); nested.className = "cmdref";
    item.appendChild(nested);
    mount.appendChild(item);
    mount = nested;
  }
  (node.commands || []).forEach(child => renderCommandReference(child, mount, depth + 1));
}
const data = readJSON("data-json");
const accounts = readJSON("accounts-json");
const downloads = readJSON("downloads-json");
const docs = readJSON("docs-json");
renderSummary(data);
document.getElementById("approx-note").textContent = accounts.approximation_note || "";
makeTable("accounts", accounts.accounts || [], [
  { key: "username", label: "Cuenta" }, { key: "mode", label: "Modo" },
  { key: "paused", label: "Pausada", fmt: v => (v ? "Si" : "No") },
  { key: "needs_review", label: "Revision", fmt: v => (v ? "Si" : "No") },
  { key: "videos_downloaded", label: "Videos" },
  { key: "total_disk_bytes", label: "Tamano en disco", fmt: fmtBytes },
  { key: "backfill_status", label: "Backfill" },
  { key: "backfill_done", label: "Hecho" }, { key: "backfill_total", label: "Total" }]);
makeTable("downloads", downloads.downloads || [], [
  { key: "downloaded_at", label: "Fecha" }, { key: "username", label: "Cuenta" },
  { key: "status", label: "Estado" }, { key: "title", label: "Titulo" },
  { key: "file_size", label: "Tamano", fmt: fmtBytes },
  { key: "error_category", label: "Categoria" }]);
const failures = document.getElementById("failures");
Object.entries(downloads.failures_by_category || {}).forEach(([cat, count]) => {
  failures.appendChild(el("dt", cat)); failures.appendChild(el("dd", String(count))); });
if (!Object.keys(downloads.failures_by_category || {}).length) { failures.appendChild(el("dd", "-")); }
document.getElementById("about").textContent = docs.about || "";
if (docs.command_reference) {
  renderCommandReference(docs.command_reference, document.getElementById("cmdref"), 0);
} else {
  document.getElementById("cmdref").textContent = docs.command_reference_placeholder || "";
}
</script>
</body>
</html>
"""


def _index_html(
    data_payload: dict, accounts_payload: dict, downloads_payload: dict, docs_payload: dict
) -> str:
    """Fill the template: JSON payloads embedded (file:// friendly, no fetch)."""
    return (
        _TEMPLATE.replace("__DATA_JSON__", _dump(data_payload))
        .replace("__ACCOUNTS_JSON__", _dump(accounts_payload))
        .replace("__DOWNLOADS_JSON__", _dump(downloads_payload))
        .replace("__DOCS_JSON__", _dump(docs_payload))
    )


async def render_site(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    output_dir: str | Path,
    command_reference: Any = None,
    now_fn: Callable[[], datetime] | None = None,
) -> list[Path]:
    """Render the 10.3 static dashboard into ``output_dir`` and return the paths.

    Pure file writer: no server, no port, no dynamic endpoint. The command
    reference arrives as a JSON-safe parameter (services/ never imports cli/;
    the scheduler job passes None and gets the placeholder). Deterministic
    under an injected ``now_fn``.
    """
    now = (now_fn or _utc_now)()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = settings.static_site_history_limit or HISTORY_LIMIT_FALLBACK

    data_payload = {
        "summary": await _summary(session_factory, settings, now),
        "generated_at": now.isoformat(),
    }
    accounts_payload = {
        "approximation_note": APPROXIMATION_NOTE,
        "accounts": await _accounts(session_factory),
    }
    downloads_payload = await _downloads(session_factory, limit)
    docs_payload = {
        "about": ABOUT_BLURB,
        "command_reference": command_reference,
        "command_reference_placeholder": (
            None if command_reference is not None else NO_COMMAND_REFERENCE_PLACEHOLDER
        ),
    }

    _write_atomic(out_dir / "accounts.json", _dump(accounts_payload))
    _write_atomic(out_dir / "downloads.json", _dump(downloads_payload))
    _write_atomic(out_dir / "data.json", _dump(data_payload))
    _write_atomic(
        out_dir / "index.html",
        _index_html(data_payload, accounts_payload, downloads_payload, docs_payload),
    )
    return [
        out_dir / name for name in ("index.html", "accounts.json", "downloads.json", "data.json")
    ]
