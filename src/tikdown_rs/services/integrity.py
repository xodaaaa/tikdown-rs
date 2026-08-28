"""Verificación de integridad — services/integrity.py (§4.6).

Tamaño > 0 + SHA-256 + ffprobe (pista de vídeo, duración > 0, codecs).
I/O pesada a asyncio.to_thread (T12); ffprobe con '--' antes de la ruta (T13).

story: e09s01
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path

LOG = logging.getLogger("tikdown_rs.integrity")


def ffprobe_args(path: Path) -> list[str]:
    """Argumentos de ffprobe con '--' antes de la ruta (T13)."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    return [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        "--",  # T13: la ruta va tras -- (nunca se interpreta como opción)
        str(path),
    ]


def _sha256(path: Path) -> str:
    """SHA-256 del archivo (I/O pesada — el llamador la envuelve en to_thread, T12)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_video(path: Path) -> dict:
    """Verifica un vídeo: tamaño > 0 + SHA-256 + ffprobe (§4.6).

    Returns: {"ok": bool, "sha256": str|None, "size": int, "has_video_stream": bool,
              "duration": float|None, "reason": str|None}
    """
    if not path.exists():
        return {"ok": False, "reason": "archivo no existe", "size": 0}
    size = path.stat().st_size
    if size == 0:
        return {"ok": False, "reason": "tamaño 0", "size": 0, "sha256": None}

    digest = _sha256(path)
    # ffprobe con '--' (T13)
    try:
        proc = subprocess.run(
            ffprobe_args(path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        has_video = False
        duration = None
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    has_video = True
                if stream.get("duration"):
                    duration = float(stream["duration"])
            if duration is None:
                fmt = data.get("format", {})
                if fmt.get("duration"):
                    duration = float(fmt["duration"])
        return {
            "ok": has_video and (duration or 0) > 0,
            "sha256": digest,
            "size": size,
            "has_video_stream": has_video,
            "duration": duration,
            "reason": (
                None
                if (has_video and (duration or 0) > 0)
                else "sin pista de vídeo o duración 0"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - ffprobe falló
        LOG.warning("integrity.ffprobe_failed", extra={"exc": repr(exc)})
        return {
            "ok": False,
            "sha256": digest,
            "size": size,
            "has_video_stream": False,
            "duration": None,
            "reason": f"ffprobe falló: {exc}",
        }
