"""e04s01 — archivo de deduplicación: L-C8 (formatos), T47 (malformada), T24."""
# story: e04s01

from tikdown_rs.core.archive import DownloadArchive


def test_parser_ambos_formatos_lc8(tmp_path):
    """L-C8: reconoce 'tiktok <id>' y '<id>' pelado (ID = último token)."""
    f = tmp_path / "archive.txt"
    f.write_text("tiktok 123456\n789012\ntiktok 345678\n", encoding="utf-8")
    arch = DownloadArchive(f)
    assert arch.contains("123456") is True
    assert arch.contains("789012") is True
    assert arch.contains("345678") is True
    assert arch.contains("999999") is False


def test_parser_tolera_ultima_linea_malformada_t47(tmp_path):
    """T47: última línea parcial (corte a mitad de write) se tolera y salta."""
    f = tmp_path / "archive.txt"
    f.write_text("tiktok 123456\n789012\ntiktok 34", encoding="utf-8")  # última cortada
    arch = DownloadArchive(f)
    assert arch.contains("123456") is True
    assert arch.contains("789012") is True
    # La línea malformada no rompe el parseo ni produce falsos IDs
    assert arch.contains("34") is False


def test_add_no_duplica(tmp_path):
    """add() tolera duplicados físicos (no duplica)."""
    f = tmp_path / "archive.txt"
    f.write_text("tiktok 123456\n", encoding="utf-8")
    arch = DownloadArchive(f)
    arch.add("123456")  # ya presente
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    assert lines.count("tiktok 123456") == 1


def test_discard_reescritura_atomica(tmp_path):
    """discard() con reescritura — elimina la entrada (T24: previo al reintento)."""
    f = tmp_path / "archive.txt"
    f.write_text("tiktok 123456\ntiktok 789012\n", encoding="utf-8")
    arch = DownloadArchive(f)
    arch.discard("123456")
    assert arch.contains("123456") is False
    assert arch.contains("789012") is True
