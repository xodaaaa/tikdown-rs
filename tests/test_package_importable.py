"""e01s01 — el paquete tikdown_rs es importable (TDD tracer bullet)."""
# story: e01s01


def test_package_importable():
    import tikdown_rs

    assert tikdown_rs.__name__ == "tikdown_rs"
