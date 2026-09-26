"""Shared exception types for configuration and runtime failures.

Trampas neutralizadas: T-DEPLOY-8 (fail-fast con mensajes accionables). Regla: 11.1, 5.1.
"""


class ConfigurationError(Exception):
    """A configuration problem the operator must fix before the daemon can run."""
