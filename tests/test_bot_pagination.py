"""Pure pagination for the Telegram bot /list command (plan section 6.4).

Covers render_list_page, build_list_keyboard and parse_list_page_callback.
"""

import time
from collections import namedtuple

import pytest

from tikdown_rs.bot.pagination import (
    CALLBACK_TTL_SECONDS,
    LIST_CALLBACK_PREFIX,
    LIST_PAGE_SIZE,
    build_list_keyboard,
    parse_list_page_callback,
    render_list_page,
)

Account = namedtuple("Account", "username backfill_status video_count")


def make_accounts(n: int) -> list[Account]:
    return [Account(f"user{i}", "idle", i) for i in range(n)]


# --- render_list_page -------------------------------------------------------


class TestRenderListPage:
    def test_empty_list(self):
        text, page, total_pages = render_list_page([])
        assert text == "No hay cuentas"
        assert page == 0
        assert total_pages == 0

    def test_single_page(self):
        text, page, total_pages = render_list_page(make_accounts(3))
        assert total_pages == 1
        assert page == 0
        assert all(f"@user{i}" in text for i in range(3))

    def test_exact_multiple_is_one_page(self):
        _, _, total_pages = render_list_page(make_accounts(LIST_PAGE_SIZE))
        assert total_pages == 1

    def test_exact_multiple_plus_one_is_two_pages(self):
        _, _, total_pages = render_list_page(make_accounts(LIST_PAGE_SIZE + 1))
        assert total_pages == 2

    def test_total_pages_is_ceil(self):
        _, _, total_pages = render_list_page(make_accounts(11))
        assert total_pages == 3

    @pytest.mark.parametrize(("page", "expected"), [(-5, 0), (7, 2), (100, 2)])
    def test_out_of_range_page_clamps(self, page, expected):
        text, clamped, total_pages = render_list_page(make_accounts(11), page=page)
        assert 0 <= clamped < total_pages
        assert clamped == expected
        assert f"@user{expected * LIST_PAGE_SIZE}" in text

    def test_page_slice_is_correct(self):
        accounts = make_accounts(12)
        text, page, _ = render_list_page(accounts, page=1)
        assert page == 1
        for i in range(5):
            assert f"@user{i}" not in text
        for i in range(5, 10):
            assert f"@user{i}" in text
        assert "@user11" not in text

    def test_escapes_html_in_username(self):
        accounts = [Account("<b>evil</b>", "idle", 0)]
        text, _, _ = render_list_page(accounts)
        assert "&lt;b&gt;evil&lt;/b&gt;" in text
        assert "<b>evil</b>" not in text

    def test_escapes_html_in_backfill_status(self):
        accounts = [Account("user", "<script>", 0)]
        text, _, _ = render_list_page(accounts)
        assert "&lt;script&gt;" in text

    def test_null_video_count_renders(self):
        accounts = [Account("user", "idle", None)]
        text, _, _ = render_list_page(accounts)
        assert "@user" in text


# --- build_list_keyboard ----------------------------------------------------


class TestBuildListKeyboard:
    def test_first_page_omits_prev(self):
        rows = build_list_keyboard(0, 3, ts=1700000000)
        buttons = [b for row in rows for b in row]
        assert all(b["text"] != "◀️" for b in buttons)
        assert any(b["text"] == "▶️" for b in buttons)

    def test_last_page_omits_next(self):
        rows = build_list_keyboard(2, 3, ts=1700000000)
        buttons = [b for row in rows for b in row]
        assert any(b["text"] == "◀️" for b in buttons)
        assert all(b["text"] != "▶️" for b in buttons)

    def test_middle_page_has_both(self):
        rows = build_list_keyboard(1, 3, ts=1700000000)
        buttons = [b for row in rows for b in row]
        texts = [b["text"] for b in buttons]
        assert "◀️" in texts and "▶️" in texts

    def test_single_page_has_no_buttons(self):
        assert build_list_keyboard(0, 1, ts=1700000000) == []

    def test_callback_data_format_and_target_page(self):
        rows = build_list_keyboard(1, 3, ts=1700000000)
        buttons = {b["text"]: b for row in rows for b in row}
        assert buttons["◀️"]["callback_data"] == f"{LIST_CALLBACK_PREFIX}1700000000:0"
        assert buttons["▶️"]["callback_data"] == f"{LIST_CALLBACK_PREFIX}1700000000:2"

    def test_callback_data_within_64_bytes(self, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 4102444800.0)  # year 2100
        for page, total in [(0, 2), (1, 3), (10**6, 10**6 + 1)]:
            rows = build_list_keyboard(page, total)
            for row in rows:
                for button in row:
                    assert len(button["callback_data"].encode()) <= 64


# --- parse_list_page_callback -----------------------------------------------


class TestParseListPageCallback:
    def test_valid_within_ttl(self):
        page, ok = parse_list_page_callback("listp:1700000000:2", now=1700000030)
        assert ok is True
        assert page == 2

    def test_valid_at_ttl_boundary(self):
        page, ok = parse_list_page_callback("listp:1700000000:0", now=1700000060)
        assert ok is True
        assert page == 0

    def test_expired(self):
        page, ok = parse_list_page_callback("listp:1700000000:0", now=1700000061)
        assert ok is False
        assert page is None

    def test_wrong_prefix(self):
        page, ok = parse_list_page_callback("other:1700000000:0", now=1700000000)
        assert ok is False
        assert page is None

    def test_garbage_page(self):
        page, ok = parse_list_page_callback("listp:1700000000:abc", now=1700000000)
        assert ok is False
        assert page is None

    def test_garbage_timestamp(self):
        page, ok = parse_list_page_callback("listp:xx:0", now=1700000000)
        assert ok is False
        assert page is None

    def test_missing_page_component(self):
        page, ok = parse_list_page_callback("listp:1700000000", now=1700000000)
        assert ok is False
        assert page is None

    def test_negative_page_clamped_to_zero(self):
        page, ok = parse_list_page_callback("listp:1700000000:-3", now=1700000000)
        assert ok is True
        assert page == 0

    def test_ttl_constant(self):
        assert CALLBACK_TTL_SECONDS == 60
        assert LIST_PAGE_SIZE == 5
