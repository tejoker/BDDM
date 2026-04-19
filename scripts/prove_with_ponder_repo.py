"""Repo/snapshot helper utilities for prove_with_ponder."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def create_snapshot_repo(project_root: Path) -> tuple[Path, Path]:
    """Create a temporary committed snapshot when the source repo has no commits."""
    tmp_root = Path(tempfile.mkdtemp(prefix="desol-dojo-snapshot-"))
    snapshot_repo = tmp_root / "repo"

    def _ignore(_src: str, names: list[str]) -> set[str]:
        ignored = {".lake", "__pycache__", ".venv", ".git", ".mypy_cache", ".pytest_cache"}
        return {n for n in names if n in ignored}

    shutil.copytree(project_root, snapshot_repo, ignore=_ignore)

    def _git(args: list[str]) -> None:
        subprocess.run(
            ["git", *args],
            cwd=snapshot_repo,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    _git(["init", "-q"])
    _git(["config", "user.name", "desol-ci"])
    _git(["config", "user.email", "desol-ci@example.com"])
    _git(["add", "."])
    _git(["commit", "-q", "-m", "snapshot"])
    return snapshot_repo, tmp_root


def repo_has_commit(project_root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except Exception:
        return False

