import time

import pytest

from bring_fast import update


def _fake_repo(monkeypatch, tmp_path, *, behind=3, fetch_ok=True, merge_ok=True):
    monkeypatch.setattr(update, "STATUS_FILE", tmp_path / "update-status.json")
    monkeypatch.setattr(update, "REPO", tmp_path)

    def fake_git(*args, timeout=45):
        out = ""
        code = 0
        err = ""
        if args[0] == "fetch":
            if not fetch_ok:
                code, err = 1, "fatal: unable to access github"
        elif args[0] == "merge":
            if not merge_ok:
                code, err = 1, "fatal: not possible to fast-forward"
        elif args[:2] == ("rev-parse", "HEAD"):
            out = "aaa111\n"
        elif args[:2] == ("rev-parse", "origin/main"):
            out = "bbb222\n"
        elif args[:2] == ("rev-list", "--count") and str(args[2]).startswith("HEAD.."):
            out = f"{behind}\n"
        elif args[:2] == ("rev-list", "--count"):
            out = "0\n"
        elif args[0] == "log":
            out = "bbb222 feat: something\n"

        class R:
            returncode = code
            stdout = out
            stderr = err

        return R()

    monkeypatch.setattr(update, "_git", fake_git)


def test_status_reports_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "STATUS_FILE", tmp_path / "update-status.json")
    monkeypatch.setattr(update, "REPO", tmp_path)

    def fake_git(*args, timeout=45):
        cmd = args[0] if args else ""
        out = ""
        if cmd == "fetch":
            pass
        elif args[:2] == ("rev-parse", "HEAD"):
            out = "aaa111\n"
        elif args[:2] == ("rev-parse", "origin/main"):
            out = "bbb222\n"
        elif args[:2] == ("rev-list", "--count") and str(args[2]).startswith("HEAD.."):
            out = "3\n"
        elif args[:2] == ("rev-list", "--count"):
            out = "0\n"
        elif args[0] == "log":
            out = "bbb222 feat: something\n"
        elif args[0] == "status":
            out = ""
        class R:
            returncode = 0
            stdout = out
            stderr = ""
        return R()

    monkeypatch.setattr(update, "_git", fake_git)
    st = update.status(fetch=True)
    assert st["available"] is True
    assert st["behind"] == 3
    assert st["local"] == "aaa111"
    saved = update.load_saved()
    assert saved["behind"] == 3


def test_status_carries_the_offline_recheck_cadence(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    st = update.status(fetch=True)
    assert st["recheck_seconds"] == 600
    assert st["fetched"] is True
    assert st["offline"] is False
    assert st["fetched_at"] == st["checked_at"]


def test_a_failed_fetch_keeps_the_last_time_github_answered(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    good = update.status(fetch=True)

    _fake_repo(monkeypatch, tmp_path, fetch_ok=False)
    offline = update.status(fetch=True)
    assert offline["offline"] is True
    assert offline["fetched"] is False
    assert offline["fetched_at"] == good["fetched_at"]
    # Local refs still answer, so the banner keeps telling the truth offline.
    assert offline["behind"] == 3
    assert offline["available"] is True


def test_apply_gives_the_page_a_countdown_to_fill(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(update, "schedule_restart", lambda delay=0: True)
    out = update.apply()
    assert out["applied"] is True
    assert out["restarting"] is True
    assert out["restart_in"] == update.RESTART_DELAY
    assert out["ready_in"] > out["restart_in"]


def test_apply_without_a_service_promises_no_restart(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(update, "schedule_restart", lambda delay=0: False)
    out = update.apply()
    assert out["applied"] is True
    assert out["restarting"] is False
    assert out["restart_in"] == 0
    assert out["ready_in"] == 0
    assert "Restart it" in out["message"]


def test_a_failed_merge_never_promises_a_restart(monkeypatch, tmp_path):
    _fake_repo(monkeypatch, tmp_path, merge_ok=False)
    calls = []
    monkeypatch.setattr(update, "schedule_restart", lambda delay=0: calls.append(delay) or True)
    out = update.apply()
    assert out["applied"] is False
    assert out["restarting"] is False
    assert calls == []


def test_the_restart_waits_until_the_response_is_out(monkeypatch):
    """Restarting inline would kill the reply the countdown needs."""
    fired = []
    monkeypatch.setattr(update, "_can_restart", lambda: True)
    monkeypatch.setattr(update, "_restart", lambda: fired.append(True) or True)

    assert update.schedule_restart(0.05) is True
    assert fired == []
    for _ in range(100):
        if fired:
            break
        time.sleep(0.01)
    assert fired == [True]


def test_no_service_means_no_scheduled_restart(monkeypatch):
    monkeypatch.setattr(update, "_can_restart", lambda: False)
    monkeypatch.setattr(update, "_restart", lambda: pytest.fail("restarted without a service"))
    assert update.schedule_restart(0) is False
