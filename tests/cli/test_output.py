"""e08s02 — salida: ASCII puro (L-A5), --json, barra (T3)."""
# story: e08s02
from tikdown_rs.cli.output import ascii_markers, to_json


def test_markers_ascii_puros_la5():
    """L-A5: marcadores ASCII puros — sin glifos Unicode."""
    markers = ascii_markers()
    assert markers["ok"] == "OK"
    assert markers["error"] == "ERROR"
    assert markers["empty"] == "-"
    assert markers["alert"] == "!"
    # Sin glifos Unicode
    for v in markers.values():
        assert all(ord(c) < 128 for c in v)


def test_to_json_serializable():
    """--json: serializa dicts para scripting/bot."""
    data = {"username": "usuario", "count": 3}
    out = to_json(data)
    import json

    assert json.loads(out) == data


def test_barra_fields_corchetes_t3():
    """T3: los campos se acceden con {task.fields[clave]}, no {task.fields.clave}."""
    from tikdown_rs.cli.output import PROGRESS_COLUMNS

    # Los nombres propios no colisionan con total/completed (T3)
    for col in PROGRESS_COLUMNS:
        assert col not in ("total", "completed")


def test_barra_render_con_datos_simulados():
    """T3: render de la barra con datos simulados (no solo construir el objeto)."""
    from tikdown_rs.cli.output import render_progress_with

    # Render completo con campos procesados/correctos/fallidos/esperados
    out = render_progress_with(procesados=10, correctos=8, fallidos=1, esperados=20)
    assert "10" in out  # procesados visibles en el render
    assert "8" in out  # correctos
