"""Shared exception types for configuration and runtime failures.

Trampas neutralizadas: T-DEPLOY-8 (fail-fast con mensajes accionables). Regla: 11.1, 5.1.
"""


class ConfigurationError(Exception):
    """A configuration problem the operator must fix before the daemon can run."""


# 4.4 rule 2, M1 scope ONLY: authentication markers -> 'definitive'. Everything
# else -> 'transient' (rule 10: never definitive by default). M2 extends this
# table with rules 1, 3-10; until then the other rules are deliberately NOT
# half-implemented here. The evaluation ORDER matters (rule 1 before generic
# 403, rule 5 before rule 4): when M2 lands, keep table order, not set order.
AUTH_MARKERS: tuple[str, ...] = (
    "requiring login",
    "login required",
    "log into an account",
    "log in for access",
    "permission to view",
    "account is private",
    "captcha",
    "banned",
    "suspended",
    "session expired",
)


def classify_error(exc_or_message: str | BaseException) -> str:
    """Project-wide error classifier (4.4, T-DATA-3: ONE classifier, no parallels).

    M1 implements rule 2 only: any auth marker (case-insensitive) found in the
    FULL exception text, including the chained ``__cause__`` walk, ->
    ``'definitive'``. Everything else -> ``'transient'`` (rule 10: never
    definitive by default). Markers are matched over the complete message, never
    short isolated substrings (T-ENGINE-2: no overly broad markers).

    M2 extends the table with rules 1, 3-10; the evaluation ORDER is mandatory
    (4.4 header), so keep new rules in table order above the rule-10 default.
    """
    if isinstance(exc_or_message, BaseException):
        parts: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc_or_message
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            parts.append(str(current))
            parts.append(repr(current))
            current = current.__cause__
        haystack = " \n".join(parts).lower()
    else:
        haystack = exc_or_message.lower()
    if any(marker in haystack for marker in AUTH_MARKERS):
        return "definitive"
    return "transient"
