from __future__ import annotations

from pathlib import Path
import plistlib
import subprocess
import sys

from .config import ROOT


LABEL = "local.ab-test-archive.sync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SYNC_HOUR = 9
SYNC_MINUTE = 0


def install_schedule() -> Path:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    program = sys.executable
    plist = {
        "Label": LABEL,
        "ProgramArguments": [program, "-m", "ab_archive", "sync"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": SYNC_HOUR, "Minute": SYNC_MINUTE},
        "StandardOutPath": str(ROOT / "data" / "sync.log"),
        "StandardErrorPath": str(ROOT / "data" / "sync.err.log"),
        "RunAtLoad": False,
    }
    (ROOT / "data").mkdir(exist_ok=True)
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
