from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess

from .db import ArchiveDB
from .export import export_archive

DEFAULT_DATA_REPO = "https://github.com/leisurespecials/ab-test-archive-data.git"
# Outside ~/Documents on purpose: see the macl notes in schedule.py.
DEFAULT_CLONE_DIR = Path.home() / ".local" / "share" / "ab-archive-data"


def publish_archive(
    db: ArchiveDB,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Export the archive into a local clone of the data repo and push if anything changed.

    Returns True if a commit was pushed.
    """
    say = progress or (lambda message: None)
    repo_url = os.getenv("AB_ARCHIVE_DATA_REPO") or DEFAULT_DATA_REPO
    clone_dir = Path(os.getenv("AB_ARCHIVE_DATA_DIR") or DEFAULT_CLONE_DIR).expanduser()

    if not (clone_dir / ".git").exists():
        say(f"Cloning data repo into {clone_dir}...")
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        _git(clone_dir.parent, "clone", repo_url, str(clone_dir))
    else:
        _git(clone_dir, "pull", "--rebase", "--autostash")

    tests, changed, removed = export_archive(db, clone_dir)
    say(f"Exported {tests} tests: {changed} files changed, {removed} removed.")

    _git(clone_dir, "add", "-A")
    if not _git(clone_dir, "status", "--porcelain").stdout.strip():
        say("Data repo already up to date; nothing to push.")
        return False

    _git(clone_dir, "commit", "-m", f"Archive update: {tests} tests ({changed} files changed)")
    _git(clone_dir, "push", "origin", "HEAD")
    say("Pushed archive update to the data repo.")
    return True


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # launchd has no shell profile or global credential helper, so authenticate
    # via the gh CLI for this one command instead of changing global git config.
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    cmd = [
        "git",
        "-c", f"credential.helper=!{gh} auth git-credential",
        "-c", "user.name=ab-archive",
        "-c", "user.email=ab-archive@users.noreply.github.com",
        *args,
    ]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result
