"""A workspace is one product the team is building.

Each workspace is a self-contained uv project (with gradio installed) under
`workspaces/<name>/`. Workspaces persist between runs: a fresh build creates
it, and later "improve" runs keep working on the same files, so you can iterate
with the team like you would with a human engineering team.

Layout:
    workspaces/<name>/
        REQUIREMENTS.md     business requirements (from you)
        DESIGN.md           architecture written by the engineering lead
        CHANGELOG.md        one entry per run: feedback in, what changed
        *.py                product code, tests and app.py (flat, no packages)
        .studio/            machine state: plan.json, reviews, run history
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import settings

PROTECTED_FILES = {"pyproject.toml", "uv.lock", ".python-version"}
HIDDEN_PREFIXES = (".", "__pycache__", "_studio_")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "product"


@lru_cache(maxsize=1)
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def use_docker() -> bool:
    if settings.RUNNER == "docker":
        return True
    if settings.RUNNER == "local":
        return False
    return docker_available()


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def render(self, limit: int = 6000) -> str:
        def clip(text: str) -> str:
            text = text or "(empty)"
            return text if len(text) <= limit else text[: limit // 2] + "\n... [truncated] ...\n" + text[-limit // 2 :]

        head = "TIMED OUT" if self.timed_out else f"Exit code: {self.exit_code}"
        return f"{head}\n\n--- stdout ---\n{clip(self.stdout)}\n\n--- stderr ---\n{clip(self.stderr)}"


class Workspace:
    def __init__(self, name: str):
        self.name = slugify(name)
        self.path = settings.WORKSPACES_DIR / self.name
        self.meta = self.path / ".studio"

    # ------------------------------------------------------------------ setup
    @property
    def exists(self) -> bool:
        return (self.path / "pyproject.toml").is_file()

    def create(self, fresh: bool) -> None:
        """Create the uv project. `fresh=True` wipes any previous product."""
        if fresh and self.path.exists():
            shutil.rmtree(self.path)
        if not self.exists:
            self.path.mkdir(parents=True, exist_ok=True)
            env = self._host_uv_env()
            subprocess.run(["uv", "init", "--bare", "--no-workspace", "--python", "3.13", "--name", self.name],
                           cwd=self.path, check=True, capture_output=True, env=env)
            subprocess.run(["uv", "add", "gradio"], cwd=self.path, check=True, capture_output=True, env=env)
        self.meta.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ files
    def safe_path(self, filename: str) -> Path:
        name = (filename or "").strip()
        if name.startswith("./"):
            name = name[2:]
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(
                f"Invalid filename {filename!r}: use a plain file name like 'accounts.py' "
                "(no folders, no leading dot)."
            )
        return self.path / name

    def list_files(self) -> list[str]:
        if not self.path.exists():
            return []
        return sorted(
            p.name
            for p in self.path.iterdir()
            if p.is_file() and not p.name.startswith(HIDDEN_PREFIXES) and p.name not in PROTECTED_FILES
        )

    def read(self, filename: str) -> str:
        return self.safe_path(filename).read_text()

    def write(self, filename: str, content: str) -> None:
        path = self.safe_path(filename)
        if path.name in PROTECTED_FILES:
            raise ValueError(f"{path.name} is managed by the studio and cannot be edited.")
        path.write_text(content)

    def remove_scratch_files(self) -> list[str]:
        """Delete throwaway scripts engineers create to try things out."""
        removed = []
        for name in self.list_files():
            if name.startswith(("scratch", "_scratch")) and name.endswith(".py"):
                (self.path / name).unlink(missing_ok=True)
                removed.append(name)
        return removed

    def read_optional(self, filename: str) -> str:
        path = self.path / filename
        return path.read_text() if path.is_file() else ""

    def snapshot(self, max_chars: int = 2500) -> str:
        """Short listing of the product files with their first lines (for prompts)."""
        lines = []
        for name in self.list_files():
            text = self.read_optional(name)
            lines.append(f"- {name} ({len(text.splitlines())} lines)")
        listing = "\n".join(lines) or "(no files yet)"
        return listing[:max_chars]

    # ---------------------------------------------------------- history/meta
    def save_json(self, name: str, data) -> None:
        self.meta.mkdir(parents=True, exist_ok=True)
        (self.meta / name).write_text(json.dumps(data, indent=2, default=str))

    def load_json(self, name: str, default=None):
        path = self.meta / name
        return json.loads(path.read_text()) if path.is_file() else default

    def append_changelog(self, title: str, body: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {stamp} — {title}\n\n{body.strip()}\n"
        path = self.path / "CHANGELOG.md"
        if not path.exists():
            path.write_text("# Changelog\n\nEvery build / review / feedback round is recorded here.\n")
        with path.open("a") as fh:
            fh.write(entry)

    # ------------------------------------------------------------- execution
    def _host_uv_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Do not let the studio's own virtualenv leak into the product project.
        for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"):
            env.pop(key, None)
        return env

    def run_python(self, *args: str, timeout: int | None = None) -> RunResult:
        """Run `python <args>` inside the product's uv project (Docker if available)."""
        timeout = timeout or settings.RUN_TIMEOUT_SECONDS
        if use_docker():
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{self.path}:/workspace", "-w", "/workspace",
                # shared download cache, but a container-local venv so the host .venv is never touched
                "-v", "crew-studio-uv-cache:/root/.cache/uv",
                "-e", "UV_PROJECT_ENVIRONMENT=/tmp/venv", "-e", "UV_LINK_MODE=copy",
                "-e", "PYTHONUNBUFFERED=1",
                settings.DOCKER_IMAGE,
                "uv", "run", "--quiet", "--frozen", "python", *args,
            ]
            env, cwd = None, None
        else:
            cmd = ["uv", "run", "--quiet", "python", *args]
            env, cwd = self._host_uv_env(), self.path
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            return RunResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(-1, out, err, timed_out=True)


def list_workspaces() -> list[str]:
    return sorted(p.name for p in settings.WORKSPACES_DIR.iterdir() if (p / "pyproject.toml").is_file())
