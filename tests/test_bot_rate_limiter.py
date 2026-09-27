"""T-BOT-8 (§6.3, §2): AIORateLimiter requires the [rate-limiter] extra of python-telegram-bot.

The extra installs ``aiolimiter``; without it, constructing the bot's rate limiter
raises. This test guards the pyproject dependency declaration.
"""


def test_aiorate_limiter_constructible():
    from telegram.ext import AIORateLimiter

    limiter = AIORateLimiter(max_retries=3)
    assert limiter is not None
