"""Server-side backups and on-demand export for Bring Fast."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db

RETENTION_DAYS = int(os.environ.get("BRINGFAST_BACKUP_RETENTION_DAYS", "14"))
BACKUP_INTERVAL = int(os.environ.get("BRINGFAST_BACKUP_INTERVAL_SECONDS", str(24 * 3600)))
CHECK_INTERVAL = int(os.environ.get("BRINGFAST_BACKUP_CHECK_SECONDS", str(3600)))

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _now() -> int:
    return int(time.time())


def _iso(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def critical_paths() -> list[Path]:
    """Files and directories that must survive a restore."""
    root = db.data_dir()
    paths: list[Path] = []
    for name in (
        "bringfast.db",
        "bringfast.db-wal",
        "bringfast.db-shm",
        "master.key",
        "session.secret",
        "vapid-private.pem",
        "vapid-public.txt",
    ):
        p = root / name
        if p.exists():
            paths.append(p)
    for folder in ("receipts", "product-images"):
        p = root / folder
        if p.is_dir():
            paths.append(p)
    return paths


def validate_server_path(raw: str, *, create: bool = True) -> tuple[bool, str, Path | None]:
    """Ensure the configured path is an absolute, writable server directory."""
    text = (raw or "").strip()
    if not text:
        return False, "Enter an absolute server path.", None
    path = Path(text)
    if not path.is_absolute():
        return False, "The backup folder must be an absolute server path (for example /var/backups/bring-fast).", None
    try:
        resolved = path.resolve()
    except OSError as e:
        return False, f"Could not resolve path: {e}", None
    if resolved.exists():
        if not resolved.is_dir():
            return False, "The path exists but is not a directory.", None
    elif create:
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Could not create directory: {e}", None
    else:
        return False, "Directory does not exist.", None
    probe = resolved / ".bring-fast-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        return False, f"Directory is not writable: {e}", None
    return True, "", resolved


def _manifest() -> dict[str, Any]:
    root = db.data_dir()
    files: list[dict[str, Any]] = []
    for p in critical_paths():
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    files.append(
                        {
                            "path": child.relative_to(root).as_posix(),
                            "bytes": child.stat().st_size,
                        }
                    )
        else:
            files.append({"path": rel, "bytes": p.stat().st_size})
    return {
        "app": "bring-fast",
        "created_at": _iso(_now()),
        "data_root": str(root),
        "files": files,
    }


def build_archive(target: Path) -> Path:
    """Write a timestamped tar.gz under `target` and return its path."""
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = target / f"bring-fast-{stamp}.tar.gz"
    root = db.data_dir()
    manifest = _manifest()
    tmp_manifest = target / f".manifest-{stamp}.json"
    tmp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    try:
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(tmp_manifest, arcname="manifest.json")
            for p in critical_paths():
                arc = p.relative_to(root).as_posix()
                tar.add(p, arcname=arc)
    finally:
        tmp_manifest.unlink(missing_ok=True)
    return archive


def prune_old_backups(folder: Path, *, retention_days: int = RETENTION_DAYS) -> int:
    """Drop archives older than retention. Returns number removed."""
    if not folder.is_dir():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for path in sorted(folder.glob("bring-fast-*.tar.gz")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def run_scheduled_backup() -> dict[str, Any]:
    """Create a backup in the connected server folder when configured."""
    settings = db.get_backup_settings()
    if settings.get("status") != "connected":
        return {"ok": False, "skipped": True, "reason": "not connected"}
    folder_text = settings.get("server_path") or ""
    ok, err, folder = validate_server_path(folder_text, create=True)
    if not ok or folder is None:
        db.set_backup_status("error", error=err or "invalid path", checked_at=_now())
        return {"ok": False, "error": err}
    try:
        archive = build_archive(folder)
        removed = prune_old_backups(folder)
        ts = _now()
        db.set_backup_status("connected", error="", checked_at=ts)
        db.set_last_backup_at(ts)
        return {
            "ok": True,
            "archive": str(archive),
            "bytes": archive.stat().st_size,
            "pruned": removed,
            "last_backup_at": _iso(ts),
        }
    except OSError as e:
        db.set_backup_status("error", error=str(e), checked_at=_now())
        return {"ok": False, "error": str(e)}


def export_download_archive() -> tuple[Path, str]:
    """Build a one-off export for browser download. Caller removes the temp dir."""
    tmp_root = db.data_dir() / "tmp-exports"
    tmp_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = tmp_root / f"download-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    archive = build_archive(work)
    filename = archive.name
    final = tmp_root / filename
    shutil.move(str(archive), final)
    shutil.rmtree(work, ignore_errors=True)
    return final, filename


def connect_backup_path(raw: str) -> dict[str, Any]:
    """Validate path, mark connected, and run an immediate backup."""
    ok, err, folder = validate_server_path(raw, create=True)
    if not ok or folder is None:
        db.set_backup_path(raw)
        db.set_backup_status("error", error=err, checked_at=_now())
        return {"ok": False, "status": "error", "error": err}
    db.set_backup_path(str(folder))
    db.set_backup_status("connected", error="", checked_at=_now())
    first = run_scheduled_backup()
    return {
        "ok": True,
        "status": "connected",
        "path": str(folder),
        "backup": first,
    }


def maybe_run_daily_backup() -> dict[str, Any] | None:
    settings = db.get_backup_settings()
    if settings.get("status") != "connected":
        return None
    last = int(settings.get("last_backup_at") or 0)
    if _now() - last < BACKUP_INTERVAL:
        return None
    return run_scheduled_backup()


def _scheduler_loop() -> None:
    while True:
        try:
            maybe_run_daily_backup()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL)


def start_scheduler() -> None:
    """In-process daily backup when a server folder is connected."""
    if os.environ.get("BRINGFAST_BACKUP_SCHEDULER", "1").strip().lower() in ("0", "false", "no"):
        return
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, name="bring-fast-backup", daemon=True)
    thread.start()


def settings_view() -> dict[str, Any]:
    settings = db.get_backup_settings()
    return {
        **settings,
        "retention_days": RETENTION_DAYS,
        "last_backup_label": _iso(settings.get("last_backup_at")),
        "last_check_label": _iso(settings.get("last_check_at")),
    }


def main() -> None:
    import sys

    db.connect()
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "run").strip().lower()
    if cmd == "run":
        result = run_scheduled_backup()
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result.get("ok") or result.get("skipped") else 1)
    if cmd == "status":
        print(json.dumps(settings_view(), indent=2))
        raise SystemExit(0)
    print("Usage: python -m bring_fast.backup [run|status]", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
