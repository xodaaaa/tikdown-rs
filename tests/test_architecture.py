"""Architecture dependency-rule test (plan section 13.2, section 12 rule).

Trampas neutralizadas: none directly; guard for the services/ dependency rule.
Regla: 12 (services/* must not import cli/, daemon/ or yt_dlp; bot must not import cli/).
Decision: AST-based import inspection, not string matching.
"""

import ast
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parent.parent / "src" / "tikdown_rs" / "services"

FORBIDDEN_ROOT_MODULES = {"yt_dlp", "typer", "python_telegram_bot"}
FORBIDDEN_LOCAL_PREFIXES = ("tikdown_rs.cli", "tikdown_rs.daemon")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _matches(module: str, forbidden_root: set[str], forbidden_prefixes: tuple[str, ...]) -> bool:
    if module in forbidden_root or module.split(".")[0] in forbidden_root:
        return True
    return any(module == p or module.startswith(p + ".") for p in forbidden_prefixes)


def test_services_do_not_import_forbidden_modules() -> None:
    service_files = sorted(SERVICES_DIR.rglob("*.py"))
    offenders: dict[str, set[str]] = {}
    for path in service_files:
        bad = {
            m
            for m in _imported_modules(path)
            if _matches(m, FORBIDDEN_ROOT_MODULES, FORBIDDEN_LOCAL_PREFIXES)
        }
        if bad:
            offenders[str(path)] = bad
    assert not offenders, f"services/ imports forbidden modules: {offenders}"


def test_daemon_does_not_import_cli() -> None:
    daemon_dir = SERVICES_DIR.parent / "daemon"
    offenders: dict[str, set[str]] = {}
    for path in sorted(daemon_dir.rglob("*.py")):
        bad = {
            m
            for m in _imported_modules(path)
            if m == "tikdown_rs.cli" or m.startswith("tikdown_rs.cli.")
        }
        if bad:
            offenders[str(path)] = bad
    assert not offenders, f"daemon/ imports cli/: {offenders}"
