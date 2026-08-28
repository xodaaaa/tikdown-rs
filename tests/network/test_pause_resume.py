"""e07s01 — transición de estados (T35), red no penaliza (T64)."""
# story: e07s01

from tikdown_rs.core.config import Settings
from tikdown_rs.core.network_monitor import NetworkMonitor


async def test_transicion_offline_notifica_una_vez():
    """network.offline se emite una sola vez al confirmar la caída."""
    settings = Settings(_env_file=None, network_offline_threshold_consecutive_failures=2)
    monitor = NetworkMonitor(settings=settings)
    events = []

    async def _on_event(event, payload):
        events.append(event)

    monitor.on_event = _on_event
    # 1 fallo → no confirma; 2º fallo → offline
    await monitor.record_failure()
    assert monitor.state == "probing"
    await monitor.record_failure()
    assert monitor.state == "offline"
    assert events.count("network.offline") == 1
    # Un fallo más en offline → no re-notifica
    await monitor.record_failure()
    assert events.count("network.offline") == 1


async def test_transicion_online_solo_desde_offline_t35():
    """T35: network.online solo desde offline CONFIRMADO (blip no notifica)."""
    settings = Settings(_env_file=None, network_offline_threshold_consecutive_failures=2)
    monitor = NetworkMonitor(settings=settings)
    events = []

    async def _on_event(event, payload):
        events.append(event)

    monitor.on_event = _on_event
    # Blip: 1 fallo + recuperación → NO offline confirmado → sin network.online
    await monitor.record_failure()
    await monitor.record_success()
    assert "network.online" not in events

    # Caída real: 2 fallos → offline; recuperación → online con duración
    await monitor.record_failure()
    await monitor.record_failure()
    assert monitor.state == "offline"
    await monitor.record_success()
    assert monitor.state == "online"
    online_events = [e for e in events if e == "network.online"]
    assert len(online_events) == 1


def test_red_no_penaliza_t64():
    """T64: un fallo de red no consume reintentos (debe_retry siempre true)."""
    from tikdown_rs.services.videos import should_retry

    assert should_retry("network", retry_count=5, max_retry=5) is True  # techo ignorado
