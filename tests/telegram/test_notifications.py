"""e06s02 — notificaciones: clip (F-07), spool (T42/F-06), coalescing (L-I3), escape (T40/L-H7)."""

# story: e06s02
import html

from tikdown_rs.core.notifications.events import event_message
from tikdown_rs.core.notifications.telegram import clip, should_coalesce


def test_clip_sufijo_dentro_f07():
    """F-07: clip() trunca con el sufijo DENTRO del límite (4096 exactos)."""
    text = "x" * 5000
    clipped = clip(text, limit=4096)
    assert len(clipped) == 4096  # exacto
    assert clipped.endswith("...(truncado)")


def test_clip_corto_no_trunca():
    """clip() no toca mensajes cortos."""
    assert clip("hola", limit=4096) == "hola"


def test_escape_html_t40():
    """T40: el render escapa contenido dinámico (HTML)."""
    # L-H7: la plantilla ya tiene @; el render NO añade otro @
    msg = event_message("download.failed", {"username": "usuario", "error": "<b>mal</b>"})
    assert "<b>mal</b>" not in msg  # escapado
    assert "&lt;b&gt;mal&lt;/b&gt;" in msg or html.escape("<b>mal</b>") in msg


def test_no_doble_arroba_lh7():
    """L-H7: el render no produce '@@usuario'."""
    msg = event_message("download.completed", {"username": "usuario"})
    assert "@@usuario" not in msg
    assert "@usuario" in msg  # el @ viene de la plantilla, una sola vez


def test_coalescing_umbral_ge_li3():
    """L-I3: coalescing con >= umbral (no == exacto)."""
    # 5 completadas (umbral 5) → coalesce
    assert should_coalesce(count=5, threshold=5) is True
    # 6 > 5 → también (>= cubre ráfagas)
    assert should_coalesce(count=6, threshold=5) is True
    # 4 < 5 → no
    assert should_coalesce(count=4, threshold=5) is False
