"""e05s02 — sonda (T57/T74/R12), get_working_cookie (L-E3), sesiones (T32)."""

# story: e05s02
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tikdown_rs.models.models import Base, Cookie
from tikdown_rs.services.cookies import (
    PROBE_MAX_ENTRIES,
    get_working_cookie,
    probe_finds_video,
)


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_probe_max_entries_5():
    """T74/L-E4: la sonda itera PROBE_MAX_ENTRIES=5."""
    assert PROBE_MAX_ENTRIES == 5


def test_probe_itera_buscando_video_l_e4():
    """L-E4: primera entrada slideshow + segunda con vídeo → valid."""
    entries = [
        {"formats": []},  # slideshow sin vídeo
        {"formats": [{"ext": "mp4"}]},  # con vídeo
    ]
    assert probe_finds_video(entries) is True


def test_probe_todas_slideshow_inconclusive():
    """L-E4: todas las 5 slideshow → no encuentra vídeo (inconclusive)."""
    entries = [{"formats": []} for _ in range(5)]
    assert probe_finds_video(entries) is False


async def test_get_working_cookie_inconclusive_conserva_l_e3(maker):
    """L-E3: cookie inconclusive NO se rechaza (se conserva con log)."""
    from cryptography.fernet import Fernet

    from tikdown_rs.core.crypto import encrypt_cookie

    key = Fernet.generate_key().decode()
    async with maker() as s:
        s.add(
            Cookie(
                encrypted_blob=encrypt_cookie(b"blob-valid", key),
                validation_state="valid",
            )
        )
        await s.commit()

    # El validador devuelve inconclusive → la cookie se conserva
    async def _fake_validate(blob):
        return "inconclusive"

    async with maker() as s:
        cookie = await get_working_cookie(
            s,
            validate_fn=_fake_validate,
            fernet_key=key,
        )
        # L-E3: inconclusive no rechaza → devuelve la cookie
        assert cookie is not None


async def test_get_working_cookie_sin_validas_none(maker):
    """Sin cookies valid → None (backfill aborta no_cookies, F-01)."""
    async with maker() as s:
        s.add(Cookie(encrypted_blob=b"blob", validation_state="invalid"))
        await s.commit()

    from cryptography.fernet import Fernet

    async with maker() as s:
        cookie = await get_working_cookie(
            s,
            validate_fn=lambda b: "invalid",
            fernet_key=Fernet.generate_key().decode(),
        )
        assert cookie is None
