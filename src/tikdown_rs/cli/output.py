"""Salida CLI — cli/output.py (§3).

Marcadores ASCII puros (L-A5, nunca glifos Unicode), --json para scripting/bot,
barras de progreso con campos no colisionantes (T3).

story: e08s02
"""

from __future__ import annotations

import json

# L-A5: marcadores ASCII puros (OK/ERROR, -, !) — NUNCA glifos Unicode
_MARKERS = {"ok": "OK", "error": "ERROR", "empty": "-", "alert": "!"}

# T3: nombres propios NO colisionantes (nunca total/completed que mutan la barra)
PROGRESS_COLUMNS = ("procesados", "correctos", "fallidos", "esperados")


def ascii_markers() -> dict:
    """Marcadores ASCII puros para la salida CLI (L-A5)."""
    return dict(_MARKERS)


def to_json(data: dict) -> str:
    """Serializa a JSON para --json (scripting/bot, §3)."""
    return json.dumps(data, ensure_ascii=True)


def render_progress_with(**fields) -> str:
    """Render de la barra de progreso con datos simulados (T3).

    Los campos se referencian con {task.fields[clave]} (corchetes) — la sintaxis
    {task.fields.clave} lanza AttributeError en el primer render (T3).
    """
    template = (
        "{task.fields[procesados]}/{task.fields[esperados]} "
        "ok={task.fields[correctos]} fail={task.fields[fallidos]}"
    )
    return template.format(
        task=type("T", (), {"fields": dict(fields)})()
    )
