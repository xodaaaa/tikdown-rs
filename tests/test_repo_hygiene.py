"""e01s05 — Higiene de repo: .dockerignore (T15/F-04), .gitignore (§0.1), README."""

# story: e01s05
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerignore_cubre_secretos_t15():
    """T15: .dockerignore excluye secretos y no excluye README.md (F-04)."""
    p = ROOT / ".dockerignore"
    assert p.exists(), ".dockerignore debe existir"
    content = p.read_text(encoding="utf-8")
    secrets = [
        ".env",
        "fernet.key",
        "cookies",
        "*.db",
        ".git",
        ".venv",
        "__pycache__",
        "data/",
        "videos/",
    ]
    for secret in secrets:
        assert secret in content, f".dockerignore debe cubrir {secret}"
    # F-04: README.md NO debe ser un patrón de exclusión (incluido en la imagen)
    exclusion_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "README.md" not in exclusion_lines, "README.md no debe excluirse (F-04)"


def test_gitignore_cubre_secretos_01():
    """§0.1: .gitignore cubre secretos y datos."""
    p = ROOT / ".gitignore"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    items = [
        ".env",
        "*.db",
        "fernet.key",
        "cookies",
        ".venv",
        "__pycache__",
        "data/",
        "videos/",
        ".migrate.lock",
    ]
    for item in items:
        assert item in content, f".gitignore debe cubrir {item}"


def test_env_example_sin_secretos_reales():
    """§0.1: .env.example solo con valores de ejemplo/vacíos, nunca tokens reales."""
    p = ROOT / ".env.example"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    # No debe contener valores tipo token real
    assert "TELEGRAM_BOT_TOKEN=" in content
    for line in content.splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN=") or line.startswith("FERNET_KEY="):
            value = line.split("=", 1)[1].strip()
            assert not value or "ejemplo" in line or len(value) <= 12, (
                ".env.example no debe tener valores de token/clave reales"
            )


def test_readme_completo():
    """§0.1: README con disclaimer legal, qué NO commitear, backup fernet.key, naming -rs."""
    p = ROOT / "README.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "daemon run" in content, "README debe explicar cómo ejecutar"
    keywords = ["disclaimer", "responsabil"]
    assert any(k in content.lower() for k in keywords), "README debe tener disclaimer legal"
    assert "fernet.key" in content, "README debe documentar backup de fernet.key"
    assert "-rs" in content, "README debe tener nota de naming -rs"
    low = content.lower()
    assert ("no commitear" in low) or ("no commitees" in low), "README debe decir qué NO commitear"


def test_license_mit():
    """§0.1: LICENSE MIT presente."""
    p = ROOT / "LICENSE"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "MIT License" in content
