"""Static Docker configuration guards for M0 Work Unit 6 (T9).

Verifies the Dockerfile and docker-compose.yml against the trap rows of the
implementation plan (specs/Plan de implementacion tikdown-rs.md), without
invoking a docker daemon:

- T-DEPLOY-5:  runtime stage must explicitly COPY alembic.ini and alembic/,
               otherwise the first boot crashes with FileNotFoundError.
- T-DEPLOY-11: .dockerignore must keep re-including README.md (hatchling
               needs it to build the wheel) and exclude .env* (secrets).
- T-DEPLOY-12: no inline comments inside ENV instructions (Docker parser
               rejects them).
- T-DEPLOY-13: restart policy must be exactly on-failure, never
               unless-stopped (a clean daemon-stop exit 0 must stay stopped).
- T-DEPLOY-14: builder stage must be python:3.13-slim (with
               UV_PYTHON_DOWNLOADS=0); the uv distroless image may only be a
               COPY --from source, never a build stage base.
- T-CLI-5:     the Dockerfile CMD and the real typer command tree are
               verified together as ONE unit: the CMD tokens must resolve to
               registered commands in the actual CLI app.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
DOCKERIGNORE = REPO / ".dockerignore"


def dockerfile_lines() -> list[str]:
    return DOCKERFILE.read_text(encoding="utf-8").splitlines()


def stage_bounds(lines: list[str]) -> list[tuple[int, int]]:
    """Return [start, end) line-index ranges per FROM stage."""
    starts = [i for i, ln in enumerate(lines) if ln.startswith("FROM ")]
    return [
        (start, starts[j + 1] if j + 1 < len(starts) else len(lines))
        for j, start in enumerate(starts)
    ]


def builder_stage(lines: list[str]) -> list[str]:
    (start, end) = stage_bounds(lines)[0]
    return lines[start:end]


def runtime_stage(lines: list[str]) -> list[str]:
    """The LAST FROM stage is the runtime stage."""
    (start, end) = stage_bounds(lines)[-1]
    return lines[start:end]


# T-DEPLOY-14: builder base and distroless uv usage.


def test_builder_stage_from_python_slim() -> None:
    first = builder_stage(dockerfile_lines())[0]
    assert first == "FROM python:3.13-slim AS builder"


def test_distroless_uv_only_as_copy_from_source() -> None:
    lines = dockerfile_lines()
    for start, end in stage_bounds(lines):
        assert not lines[start].startswith("FROM ghcr.io/astral-sh/uv"), (
            "T-DEPLOY-14: distroless uv image must never be a build stage base"
        )
    body = "\n".join(lines)
    assert "ghcr.io/astral-sh/uv" in body, "uv binary must be copied from the official image"
    for line in lines:
        if "ghcr.io/astral-sh/uv" in line:
            assert line.lstrip().startswith("COPY --from="), line


def test_builder_env_uvs() -> None:
    builder = "\n".join(builder_stage(dockerfile_lines()))
    assert "UV_COMPILE_BYTECODE=1" in builder
    assert "UV_LINK_MODE=copy" in builder
    assert "UV_PYTHON_DOWNLOADS=0" in builder


# T-DEPLOY-13: restart policy.


def test_compose_restart_is_on_failure() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(r"^\s*restart:\s*(\S+)", text, re.MULTILINE)
    assert match is not None, "compose must declare a restart policy"
    assert match.group(1) == "on-failure"


def test_never_unless_stopped() -> None:
    for path in (DOCKERFILE, COMPOSE):
        assert "unless-stopped" not in path.read_text(encoding="utf-8"), (
            f"T-DEPLOY-13: {path.name} must never use unless-stopped"
        )


# T-DEPLOY-12: no inline comments on ENV lines.


def test_no_inline_comment_in_env() -> None:
    for line in dockerfile_lines():
        assert not re.match(r"^\s*ENV\s+.*#", line), (
            f"T-DEPLOY-12: inline comment in ENV instruction: {line!r}"
        )


# T-DEPLOY-5: runtime stage copies alembic resources explicitly.


def test_runtime_copies_alembic_resources() -> None:
    runtime = "\n".join(runtime_stage(dockerfile_lines()))
    copy_lines = [ln for ln in runtime.splitlines() if ln.lstrip().startswith("COPY ")]
    assert any("alembic.ini" in ln for ln in copy_lines), "runtime must COPY alembic.ini"
    assert any(re.search(r"\balembic/?\b", ln) for ln in copy_lines), "runtime must COPY alembic/"


def test_runtime_copies_venv_and_uses_non_root() -> None:
    runtime = "\n".join(runtime_stage(dockerfile_lines()))
    assert "/app/.venv" in runtime
    assert re.search(r"^USER\s+\S+", runtime, re.MULTILINE), "runtime must run as non-root"
    assert "ffmpeg" in runtime


# T-CLI-5: CMD verified against the real typer command tree as one unit.


def _cmd_tokens() -> list[str]:
    import json

    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^CMD\s+(\[.*\])\s*$", text, re.MULTILINE)
    assert match is not None, "Dockerfile must declare an exec-form CMD"
    tokens = json.loads(match.group(1))
    assert all(isinstance(t, str) for t in tokens)
    return tokens


def test_cmd_matches_real_command_tree() -> None:
    tokens = _cmd_tokens()
    assert tokens == ["tikdown-rs", "daemon", "run"]

    from tikdown_rs.cli.main import app

    groups = {g.name: g.typer_instance for g in app.registered_groups}
    assert "daemon" in groups, "daemon group not registered in the real typer app"
    commands = {
        c.name if c.name else c.callback.__name__.replace("_", "-")
        for c in groups["daemon"].registered_commands
    }
    assert "run" in commands, "run not registered under daemon (T-CLI-5 crash-loop trap)"


# HEALTHCHECK: exec-form, daemon healthcheck, conservative start period.


def test_healthcheck_exec_form_daemon_healthcheck() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^HEALTHCHECK[^\n]*CMD\s+(\[.*\])\s*$", text, re.MULTILINE)
    assert match is not None, "HEALTHCHECK must use exec-form (JSON array)"
    tokens = json.loads(match.group(1))
    assert "daemon" in tokens and "healthcheck" in tokens
    assert re.search(r"--start-period=\d+", text)


# T-DEPLOY-11: .dockerignore drift guards.


def test_dockerignore_reincludes_readme_and_excludes_env() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    assert re.search(r"^\.env\*", text, re.MULTILINE), "secrets must stay out of the build context"
    assert re.search(r"^!README\.md\s*$", text, re.MULTILINE), (
        "T-DEPLOY-11: hatchling needs README.md for the wheel"
    )


# docker-compose.yml hardening per §14.1.


def test_compose_hardening() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "no-new-privileges:true" in text
    assert re.search(r"cap_drop:", text)
    assert re.search(r"/tmp", text), "tmpfs for /tmp"
    assert re.search(r"max-size:\s*[\"']?10m", text)
    assert re.search(r"max-file:\s*[\"']?5", text)
    assert "/app/data" in text, "single data volume mount"
    assert "seccomp" not in text, "never seccomp=unmasked"


def test_runtime_workdir_precedes_relative_copies() -> None:
    """Regression: relative COPY destinations resolve against the CURRENT
    WORKDIR. With `WORKDIR /app` placed after the alembic COPY lines, the
    files landed in '/' and first boot died with 'alembic.ini not found'
    despite the COPY layers existing (found in the live M0 docker smoke,
    T-DEPLOY-23)."""
    lines = dockerfile_lines()
    runtime_start = max(i for i, l in enumerate(lines) if l.startswith("FROM"))
    workdir_idx = next(
        i
        for i, l in enumerate(lines[runtime_start:], start=runtime_start)
        if l.strip() == "WORKDIR /app"
    )
    for i, line in enumerate(lines[runtime_start:], start=runtime_start):
        if re.match(r"^COPY\s", line) and "alembic" in line:
            assert i > workdir_idx, (
                "alembic COPY precedes WORKDIR /app: relative destination "
                "would land in '/' instead of '/app'"
            )
