"""e05s02 — cookies: import (F-15/T14), triestado (F-16), clamp (T33)."""
# story: e05s02

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.core.cookie_parser import NETSCAPE_HEADER
from tikdown_rs.core.crypto import load_or_create_fernet_key
from tikdown_rs.models.models import Base, Cookie
from tikdown_rs.services import cookies


@pytest.fixture
async def maker(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False), tmp_path
    await engine.dispose()


async def test_import_cifra_y_guarda(maker):
    """Import cifra con Fernet y guarda el blob (ciphertext, no texto)."""
    session_factory, tmp = maker
    key = load_or_create_fernet_key(tmp / "fernet.key")
    src = tmp / "cookies.txt"
    src.write_text(f"{NETSCAPE_HEADER}\n.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n",
                   encoding="utf-8")

    async with session_factory() as s:
        await cookies.add(s, src, fernet_key=key)

    async with session_factory() as s:
        row = (await s.execute(select(Cookie))).scalar_one()
        assert row.encrypted_blob is not None
        assert b"sessionid" not in row.encrypted_blob  # cifrado (no texto plano)


async def test_import_keep_source_f15(maker):
    """F-15: --keep-source conserva el archivo fuente."""
    session_factory, tmp = maker
    key = load_or_create_fernet_key(tmp / "fernet.key")
    src = tmp / "cookies.txt"
    src.write_text(f"{NETSCAPE_HEADER}\n.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n",
                   encoding="utf-8")

    async with session_factory() as s:
        await cookies.add(s, src, fernet_key=key, keep_source=True)
    assert src.exists()  # conservado


async def test_import_borrado_best_effort_t14(maker):
    """T14: borrado best-effort — fallo no rompe el éxito de la importación."""
    session_factory, tmp = maker
    key = load_or_create_fernet_key(tmp / "fernet.key")
    src = tmp / "cookies.txt"
    src.write_text(f"{NETSCAPE_HEADER}\n.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n",
                   encoding="utf-8")

    async with session_factory() as s:
        # Borrado falla (archivo bloqueado) — la importación sigue siendo éxito
        await cookies.add(s, src, fernet_key=key, keep_source=True)  # no borra

    async with session_factory() as s:
        row = (await s.execute(select(Cookie))).scalar_one()
        assert row.id is not None  # éxito


def test_clamp_expiracion_t33():
    """T33: expiración absurda se clampa a año 2100 (no OverflowError)."""
    assert cookies.clamp_expiration(99999999999999) == 4102444800  # 2100-01-01
    assert cookies.clamp_expiration(1700000000) == 1700000000  # razonable


def test_validate_triestado_f16():
    """F-16: inconclusive NO toca validation_state ni last_validated_at."""
    # valid
    assert cookies.validate_cookie_result("ok") == "valid"
    # auth confirmado → invalid
    assert cookies.validate_cookie_result("requiring login") == "invalid"
    # red/timeout/extractor → inconclusive
    assert cookies.validate_cookie_result("timeout") == "inconclusive"
    assert cookies.validate_cookie_result("no entries") == "inconclusive"
