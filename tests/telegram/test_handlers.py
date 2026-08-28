"""e06s02 — handlers: comandos planos (§6.4), escape (T40)."""
# story: e06s02
import inspect

from tikdown_rs.daemon.telegram.handlers import COMMANDS, _esc


def test_comandos_planos_6_4():
    """§6.4: comandos planos de paridad funcional con la CLI."""
    expected = {"/stats", "/disk", "/list", "/last", "/cookies", "/check",
                "/add", "/pause", "/resume", "/notify", "/monitor", "/backfill"}
    assert expected.issubset(COMMANDS)


def test_handlers_solo_orquestan():
    """§3: handlers no duplican lógica — no importan yt_dlp."""
    import tikdown_rs.daemon.telegram.handlers as mod

    src = inspect.getsource(mod)
    assert "yt_dlp" not in src


def test_escape_html_t40():
    """T40/F-05: _esc() escapa contenido dinámico."""
    assert _esc("<b>&\"'</b>") == "&lt;b&gt;&amp;&quot;&#x27;&lt;/b&gt;"
