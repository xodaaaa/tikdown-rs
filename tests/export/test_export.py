"""e08s02 — export CSV: sanitización (T49), RFC 4180, sin wrap (L-A6)."""

# story: e08s02
from tikdown_rs.services.export import sanitize_csv_field, to_csv


def test_sanitize_formula_injection_t49():
    """T49: campos con = + - @ se sanitizan (anti-inyección de fórmulas)."""
    assert sanitize_csv_field("=cmd()") != "=cmd()"
    assert sanitize_csv_field("+SUM(A1)") != "+SUM(A1)"
    assert sanitize_csv_field("-1+1") != "-1+1"
    assert sanitize_csv_field("@user") != "@user"
    # Espacios/tabs/CR antes del operador también (lstrip \x0b\x0c)
    assert sanitize_csv_field("  =cmd()") != "  =cmd()"


def test_sanitize_no_toca_normal():
    """Campos normales no se tocan."""
    assert sanitize_csv_field("hola") == "hola"
    assert sanitize_csv_field("123") == "123"


def test_to_csv_rfc4180():
    """RFC 4180: quoting correcto con comas/entrecomillados."""
    rows = [["a,b", 'c"d', "e"]]
    out = to_csv(rows)
    assert '"a,b"' in out  # comillas RFC 4180


def test_to_csv_sin_wrap():
    """L-A6: el export sale sin wrap (líneas únicas, no truncadas a 80)."""
    long = "x" * 200
    out = to_csv([["titulo", long]])
    assert long in out  # no envuelto
