from __future__ import annotations

from pathlib import Path
import plistlib
import subprocess
import sys

from .config import ROOT


LABEL = "local.ab-archive.daily-sync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SYNC_HOUR = 9
SYNC_MINUTE = 0


def install_schedule() -> Path:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    program = sys.executable
    log_path = ROOT / "data" / "sync.log"
    err_path = ROOT / "data" / "sync.err.log"
    plist = {
        "Label": LABEL,
        "ProgramArguments": [program, "-m", "ab_archive", "sync"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": SYNC_HOUR, "Minute": SYNC_MINUTE},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(err_path),
        "RunAtLoad": False,
    }
    (ROOT / "data").mkdir(exist_ok=True)
    # macOS tags files under ~/Documents with a com.apple.macl sandbox ACL
    # pinned to whichever process last had it open. A stale tag on these two
    # log files (from a previous run under a different process identity)
    # silently blocks launchd's xpcproxy from spawning the job at all -
    # exit(78)/EX_CONFIG, zero output, no traceback. Deleting them so
    # launchd creates fresh ones on first run avoids the stale grant.
    log_path.unlink(missing_ok=True)
    err_path.unlink(missing_ok=True)
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
    return PLIST_PATH


def remove_schedule() -> None:
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False, capture_output=True)
        PLIST_PATH.unlink()


def schedule_status() -> str:
    if not PLIST_PATH.exists():
        return "not installed"
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False)
    return "installed and loaded" if LABEL in result.stdout else "installed but not loaded"
