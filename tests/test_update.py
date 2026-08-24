from bring_fast import update


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
