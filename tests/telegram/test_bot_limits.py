"""e06s01 — callback_data <= 64 (T38), expiración 60s, rate limiter (T41)."""

# story: e06s01
import time

from tikdown_rs.daemon.telegram.bot import build_callback_data, callback_expired


def test_callback_data_compacto_t38():
    """T38: callback_data <= 64 bytes con encoding presupuestado."""
    ts = int(time.time())
    cb = build_callback_data(action="pause", payload="usuario1", timestamp=ts)
    assert len(cb.encode()) <= 64, f"callback_data {len(cb.encode())} > 64 bytes"


def test_callback_expira_60s():
    """Botón con expiración real: timestamp embebido validado (60s)."""
    now = int(time.time())
    assert callback_expired(now - 30, max_age=60) is False  # reciente
    assert callback_expired(now - 90, max_age=60) is True  # expirado


def test_rate_limiter_disponible_t41():
    """T41: AIORateLimiter importable (extra [rate-limiter] obligatorio)."""
    from telegram.ext import AIORateLimiter

    limiter = AIORateLimiter(max_retries=3)
    assert limiter is not None
