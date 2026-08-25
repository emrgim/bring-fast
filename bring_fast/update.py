"""Check and apply Bring Fast updates from GitHub origin/main."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REMOTE = "origin"
BRANCH = "main"
STATUS_FILE = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast")) / "update-status.json"

# The restart replaces this process, so it waits until the apply response has
# been written. The browser counts the same seconds down instead of guessing.
RESTART_DELAY = float(os.environ.get("BRINGFAST_RESTART_DELAY", "2"))
READY_SECONDS = float(os.environ.get("BRINGFAST_RESTART_READY", "8"))
# Offline clients cannot reach GitHub, so they retry on this cadence instead.
OFFLINE_RECHECK_SECONDS = int(os.environ.get("BRINGFAST_OFFLINE_RECHECK", "600"))


def _git(*args: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rev(ref: str) -> str:
    r = _git("rev-parse", ref)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def save_status(payload: dict[str, Any]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_saved() -> dict[str, Any]:
    if not STATUS_FILE.exists():
        return {}
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def status(*, fetch: bool = True) -> dict[str, Any]:
    previous = load_saved()
    err = ""
    fetched = False
    if fetch:
        got = _git("fetch", REMOTE, BRANCH, "--quiet")
        if got.returncode != 0:
            err = (got.stderr or got.stdout or "git fetch failed").strip()
        else:
            fetched = True
    local = _rev("HEAD")
    remote = _rev(f"{REMOTE}/{BRANCH}")
    behind = 0
    ahead = 0
    if local and remote:
        b = _git("rev-list", "--count", f"HEAD..{REMOTE}/{BRANCH}")
        a = _git("rev-list", "--count", f"{REMOTE}/{BRANCH}..HEAD")
        if b.returncode == 0:
            behind = int((b.stdout or "0").strip() or 0)
        if a.returncode == 0:
            ahead = int((a.stdout or "0").strip() or 0)
    log = ""
    if behind:
        lg = _git("log", "--oneline", f"HEAD..{REMOTE}/{BRANCH}")
        log = (lg.stdout or "").strip()
    dirty = _git("status", "--porcelain")
    now = _now()
    payload = {
        "ok": not err,
        "error": err,
        "checked_at": now,
        # A failed fetch still reports the revisions git already knows, so the
        # banner keeps telling the truth about the last time GitHub answered.
        "fetched": fetched,
        "fetched_at": now if fetched else (previous.get("fetched_at") or ""),
        "offline": bool(err),
        "recheck_seconds": OFFLINE_RECHECK_SECONDS,
        "branch": BRANCH,
        "local": local[:12],
        "remote": remote[:12],
        "behind": behind,
        "ahead": ahead,
        "available": behind > 0,
        "dirty": bool((dirty.stdout or "").strip()),
        "log": log,
    }
    save_status(payload)
    return payload


def apply() -> dict[str, Any]:
    st = status(fetch=True)
    if st.get("error"):
        return {**st, "applied": False, "restarting": False}
    if not st.get("available"):
        return {**st, "applied": False, "restarting": False, "message": "Already up to date."}
    pull = _git("merge", "--ff-only", f"{REMOTE}/{BRANCH}")
    if pull.returncode != 0:
        return {
            **st,
            "applied": False,
            "restarting": False,
            "error": (pull.stderr or pull.stdout or "git merge failed").strip(),
        }
    restarting = schedule_restart(RESTART_DELAY)
    after = status(fetch=False)
    return {
        **after,
        "applied": True,
        "restarting": restarting,
        # Seconds the browser has to fill: the wait before this process goes
        # away, plus how long a fresh one takes to answer /health again.
        "restart_in": RESTART_DELAY if restarting else 0,
        "ready_in": (RESTART_DELAY + READY_SECONDS) if restarting else 0,
        "message": (
            f"Updated to {after.get('local')}. Restarting."
            if restarting
            else f"Updated to {after.get('local')}. Restart it to run the new code."
        ),
    }


def _restart() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "restart", "fast-bring"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return r.returncode == 0


def schedule_restart(delay: float = RESTART_DELAY) -> bool:
    """Restart after `delay` so the apply response reaches the browser first.

    The restart kills this process, so calling it inline would drop the
    response and leave the page with nothing to count down from.
    """
    if not _can_restart():
        return False
    timer = threading.Timer(max(delay, 0.0), _restart)
    timer.daemon = True
    timer.start()
    return True


def _can_restart() -> bool:
    """True when a fast-bring service exists to restart us.

    Without it the merge still lands, and the page says so rather than
    counting down to a restart that never happens.
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", "-p", "LoadState", "--value", "fast-bring"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (r.stdout or "").strip() == "loaded"


if __name__ == "__main__":
    print(json.dumps(status(fetch=True), indent=2))
