"""Rutas de datos — todo deriva de DATA_DIR (T8).

Nunca rutas relativas al directorio de trabajo: la base de datos, la clave
Fernet, el archivo de deduplicación y los vídeos comparten el mismo
almacenamiento persistente.

story: e01s02
"""

from __future__ import annotations

from pathlib import Path

from tikdown_rs.core.config import Settings


def videos_root(settings: Settings) -> Path:
    """Directorio raíz de vídeos descargados."""
    return settings.data_dir / "videos"


def default_outtmpl(settings: Settings) -> Path:
    """Plantilla de salida por defecto para yt-dlp (deriva de videos_root)."""
    return videos_root(settings) / "%(uploader)s" / "%(id)s.%(ext)s"
