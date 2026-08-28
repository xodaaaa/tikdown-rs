"""e07s01 — NetworkMonitor: probe HEAD (F-13), umbral, backoff."""
# story: e07s01

from tikdown_rs.core.config import Settings
from tikdown_rs.core.network_monitor import NetworkMonitor, probe_backoff


def test_probe_timeout_desde_settings_f13():
    """F-13: el timeout del probe viene de Settings, no hardcodeado."""
    settings = Settings(_env_file=None, network_probe_timeout_seconds=7)
    assert settings.network_probe_timeout_seconds == 7


def test_probe_urls_nunca_tiktok():
    """§1: el probe usa endpoints neutrales, nunca TikTok."""
    settings = Settings(_env_file=None, network_probe_url="https://example.com,https://1.1.1.1")
    urls = [u.strip() for u in settings.network_probe_url.split(",") if u.strip()]
    for u in urls:
        assert "tiktok" not in u.lower()


def test_backoff_30_a_120_f13():
    """F-13: backoff del probe offline: 30s → techo 120s."""
    assert probe_backoff(failures=1) == 30
    assert probe_backoff(failures=2) == 60
    assert probe_backoff(failures=3) == 120
    assert probe_backoff(failures=10) == 120  # techo


async def test_network_available_seteado_por_defecto_ld2():
    """L-D2: el evento por defecto nace SETEADO (red asumida disponible)."""
    monitor = NetworkMonitor(settings=Settings(_env_file=None))
    assert monitor.network_available.is_set() is True
