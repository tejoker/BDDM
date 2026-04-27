"""Repo/snapshot helper utilities for prove_with_ponder."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _materialize_repl_compat(snapshot_repo: Path) -> None:
    """Ensure path dependency `third_party/repl_compat` exists in snapshots."""
    try:
        lakefile = snapshot_repo / "lakefile.toml"
        if not lakefile.exists():
            return
        txt = lakefile.read_text(encoding="utf-8", errors="replace")
        if "third_party/repl_compat" not in txt:
            return
        target = snapshot_repo / "third_party" / "repl_compat"
        if target.exists():
            return
        canonical = Path(__file__).resolve().parent.parent / "third_party" / "repl_compat"
        if not canonical.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(canonical, target_is_directory=True)
        except Exception:
            shutil.copytree(canonical, target)
    except Exception:
        # Best effort only; caller handles downstream failures.
        return


def _materialize_missing_main(snapshot_repo: Path) -> None:
    """Create a minimal Main.lean when lakefile expects it but it's absent."""
    try:
        lakefile = snapshot_repo / "lakefile.toml"
        if not lakefile.exists():
            return
        txt = lakefile.read_text(encoding="utf-8", errors="replace")
        if 'root = "Main"' not in txt:
            return
        main_file = snapshot_repo / "Main.lean"
        if main_file.exists():
            return
        main_file.write_text("def main : IO Unit := pure ()\n", encoding="utf-8")
    except Exception:
        return


def create_snapshot_repo(project_root: Path) -> tuple[Path, Path]:
    """Create a temporary committed snapshot when the source repo has no commits."""
    tmp_root = Path(tempfile.mkdtemp(prefix="desol-dojo-snapshot-"))
    snapshot_repo = tmp_root / "repo"

    def _ignore(_src: str, names: list[str]) -> set[str]:
        ignored = {".lake", "__pycache__", ".venv", ".git", ".mypy_cache", ".pytest_cache"}
        return {n for n in names if n in ignored}

    shutil.copytree(project_root, snapshot_repo, ignore=_ignore)
    _materialize_repl_compat(snapshot_repo)
    _materialize_missing_main(snapshot_repo)

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
