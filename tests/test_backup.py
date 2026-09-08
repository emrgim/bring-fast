"""Settings backup: server path, connect, scheduled dump, download."""

from __future__ import annotations

import json
import tarfile
import time

import pytest


def _signup(client, email="backup@example.com"):
    client.post(
        "/login",
        data={"email": email, "password": "secret1", "intent": "signup"},
        follow_redirects=True,
    )


def test_validate_server_path_rejects_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path / "data"))
    from bring_fast import backup

    ok, err, resolved = backup.validate_server_path("relative/backups")
    assert not ok
    assert "absolute" in err.lower()
    assert resolved is None


def test_validate_server_path_creates_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path / "data"))
    from bring_fast import backup

    target = tmp_path / "backups" / "bring-fast"
    ok, err, resolved = backup.validate_server_path(str(target), create=True)
    assert ok, err
    assert resolved == target.resolve()
    assert resolved.is_dir()


def test_connect_stores_state_and_writes_archive(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    backup_dir = tmp_path / "server-backups"
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    from bring_fast import backup, db

    _signup(client)
    r = client.post("/settings/backup/connect", json={"server_path": str(backup_dir)})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    settings = db.get_backup_settings()
    assert settings["status"] == "connected"
    assert settings["server_path"] == str(backup_dir.resolve())
    assert settings["last_backup_at"]
    archives = list(backup_dir.glob("bring-fast-*.tar.gz"))
    assert len(archives) == 1
    assert archives[0].stat().st_size > 0


def test_connect_rejects_bad_path(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    from bring_fast import db

    _signup(client, "bad@example.com")
    r = client.post("/settings/backup/connect", json={"server_path": "not-absolute"})
    assert r.status_code == 400
    settings = db.get_backup_settings()
    assert settings["status"] == "error"
    assert settings["last_check_error"]


def test_build_archive_includes_db_and_manifest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    from bring_fast import backup, db

    db.connect().close()
    out = tmp_path / "out"
    archive = backup.build_archive(out)
    assert archive.exists()
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "manifest.json" in names
    assert "bringfast.db" in names
    manifest = json.loads(tarfile.open(archive, "r:gz").extractfile("manifest.json").read())
    assert manifest["app"] == "bring-fast"


def test_download_backup_requires_login(client):
    r = client.get("/settings/backup/download", follow_redirects=False)
    assert r.status_code in (303, 307, 401)


def test_download_backup_returns_gzip(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    _signup(client, "dl@example.com")
    r = client.get("/settings/backup/download")
    assert r.status_code == 200
    assert "gzip" in (r.headers.get("content-type") or "")
    assert r.content[:2] == b"\x1f\x8b"


def test_scheduled_backup_skips_when_disconnected(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    from bring_fast import backup

    result = backup.run_scheduled_backup()
    assert result.get("skipped") is True


def test_prune_old_backups(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BRINGFAST_DATA", str(data))
    from bring_fast import backup

    folder = tmp_path / "keep"
    folder.mkdir()
    old = folder / "bring-fast-old.tar.gz"
    old.write_bytes(b"old")
    old_time = time.time() - 20 * 86400
    import os

    os.utime(old, (old_time, old_time))
    new = folder / "bring-fast-new.tar.gz"
    new.write_bytes(b"new")
    removed = backup.prune_old_backups(folder, retention_days=14)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_settings_page_shows_backup_section(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BRINGFAST_DATA", str(tmp_path / "data"))
    _signup(client, "ui@example.com")
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Backup folder (server path)" in page.text
    assert "Connect to backup" in page.text
    assert "Download backup" in page.text
