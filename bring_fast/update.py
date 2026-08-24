"""Check and apply Bring Fast updates from GitHub origin/main."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REMOTE = "origin"
BRANCH = "main"
STATUS_FILE = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast")) / "update-status.json"


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
    err = ""
    if fetch:
        got = _git("fetch", REMOTE, BRANCH, "--quiet")
        if got.returncode != 0:
            err = (got.stderr or got.stdout or "git fetch failed").strip()
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
    payload = {
        "ok": not err,
        "error": err,
        "checked_at": _now(),
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
        return {**st, "applied": False}
    if not st.get("available"):
        return {**st, "applied": False, "message": "Already up to date."}
    pull = _git("merge", "--ff-only", f"{REMOTE}/{BRANCH}")
    if pull.returncode != 0:
        return {
            **st,
            "applied": False,
            "error": (pull.stderr or pull.stdout or "git merge failed").strip(),
        }
    restarted = _restart()
    after = status(fetch=False)
    return {
        **after,
        "applied": True,
        "restarted": restarted,
        "message": f"Updated to {after.get('local')}.",
    }


def _restart() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "restart", "fast-bring"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return r.returncode == 0


if __name__ == "__main__":
    print(json.dumps(status(fetch=True), indent=2))
