"""Exportación de vídeos — services/export.py (§3, T49).

CSV con csv.writer stdlib (RFC 4180) + sanitización anti-inyección de fórmulas
(T49/F-11, OWASP/CWE-1236). Salida sin wrap ni markup (L-A6).

story: e08s02
"""

from __future__ import annotations

import csv
import io

# T49: operadores peligrosos al inicio (inyección de fórmulas)
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_csv_field(value: str) -> str:
    """Sanitiza un campo CSV contra inyección de fórmulas (T49).

    lstrip(" \\t\\r\\n\\x0b\\x0c") ANTES del chequeo de operador (F-11).
    """
    stripped = value.lstrip(" \t\r\n\x0b\x0c")
    if stripped.startswith(_FORMULA_PREFIXES):
        # Prefijo seguro: apóstrofe inicial evita interpretación como fórmula
        return "'" + value
    return value


def to_csv(rows: list[list]) -> str:
    """Serializa filas a CSV (RFC 4180, csv.writer stdlib)."""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow([sanitize_csv_field(str(c)) for c in row])
    return buf.getvalue()
