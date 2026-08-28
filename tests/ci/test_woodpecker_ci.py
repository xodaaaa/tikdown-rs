"""Tests para la config de CI Woodpecker (e10s01).

Verifica la estructura del pipeline definido en .woodpecker.yml:
- existe en la raíz del repo
- define los pasos lint, test y docker con los comandos esperados
- documenta la lección operativa L-K4 (fallo en 0s = billing/runner, no código)
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WOODPECKER = REPO_ROOT / ".woodpecker.yml"


@pytest.fixture(scope="module")
def wpc_yaml() -> dict:
    import yaml

    with WOODPECKER.open() as f:
        return yaml.safe_load(f)


def test_woodpecker_file_exists() -> None:
    assert WOODPECKER.exists(), ".woodpecker.yml debe existir en la raíz"


def test_top_level_steps_defined(wpc_yaml: dict) -> None:
    assert isinstance(wpc_yaml, dict)
    assert "steps" in wpc_yaml, "Woodpecker usa steps: a nivel top (no pipeline:)"
    assert isinstance(wpc_yaml["steps"], list)
    assert len(wpc_yaml["steps"]) >= 3


def test_three_pipeline_steps_present(wpc_yaml: dict) -> None:
    names = {s.get("name") for s in wpc_yaml["steps"]}
    assert {"lint", "test", "docker"} <= names


def test_lint_step_runs_ruff_check_and_format(wpc_yaml: dict) -> None:
    lint = next(s for s in wpc_yaml["steps"] if s.get("name") == "lint")
    commands = " ".join(lint.get("commands", []))
    assert "ruff check" in commands
    assert "ruff format --check" in commands


def test_test_step_runs_pytest_with_coverage(wpc_yaml: dict) -> None:
    test = next(s for s in wpc_yaml["steps"] if s.get("name") == "test")
    commands = " ".join(test.get("commands", []))
    assert "pytest" in commands
    assert "--cov" in commands
    assert "--cov-report=xml" in commands


def test_docker_step_builds_and_smokes(wpc_yaml: dict) -> None:
    docker = next(s for s in wpc_yaml["steps"] if s.get("name") == "docker")
    commands = " ".join(docker.get("commands", []))
    assert "docker build" in commands
    assert "docker run --rm" in commands
    assert "--version" in commands  # smoke F-22: detecta problemas de imagen


def test_when_event_includes_push_and_pr(wpc_yaml: dict) -> None:
    """El pipeline debe ejecutarse en push y pull_request.

    Woodpecker permite `when` a nivel top-level (aplica a todos los steps) o
    por step. Aceptamos ambos; solo exigimos que el evento esté cubierto.
    """
    rules = wpc_yaml.get("when")
    if rules is None:
        rules = []
        for step in wpc_yaml["steps"]:
            when = step.get("when")
            if when is not None:
                rules.extend(when if isinstance(when, list) else [when])
    events = []
    for rule in rules if isinstance(rules, list) else [rules]:
        ev = rule.get("event", [])
        events.extend(ev if isinstance(ev, list) else [ev])
    assert "push" in events, "pipeline no corre en push"
    assert "pull_request" in events, "pipeline no corre en pull_request"


def test_l_k4_lesson_documented(wpc_yaml: dict) -> None:
    """L-K4: un fallo en 0s de todos los pasos suele ser billing/runner, no código."""
    text = WOODPECKER.read_text()
    assert "L-K4" in text
