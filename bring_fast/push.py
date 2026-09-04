"""Web Push (VAPID) so a new invoice can notify the installed app."""

from __future__ import annotations

import base64
import json
import subprocess
import threading
from pathlib import Path


def _dir() -> Path:
    from . import db

    db.DATA.mkdir(parents=True, exist_ok=True)
    return db.DATA


def _priv_path() -> Path:
    return _dir() / "vapid-private.pem"


def _pub_path() -> Path:
    return _dir() / "vapid-public.txt"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def ensure_keys() -> tuple[str, str]:
    priv_p, pub_p = _priv_path(), _pub_path()
    if priv_p.exists() and pub_p.exists():
        return priv_p.read_text(encoding="utf-8"), pub_p.read_text(encoding="utf-8").strip()
    subprocess.run(
        ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(priv_p)],
        check=True,
        capture_output=True,
    )
    der = subprocess.check_output(
        ["openssl", "ec", "-in", str(priv_p), "-pubout", "-conv_form", "uncompressed", "-outform", "DER"],
        stderr=subprocess.DEVNULL,
    )
    pub = _b64(der[-65:])
    pub_p.write_text(pub, encoding="utf-8")
    try:
        priv_p.chmod(0o600)
    except OSError:
        pass
    return priv_p.read_text(encoding="utf-8"), pub


def public_key() -> str:
    return ensure_keys()[1]


def send(user_id: int, title: str, body: str, url: str = "/purchases") -> None:
    from . import db

    if not db.get_notify(user_id):
        return
    subs = db.list_push_subs(user_id)
    if not subs:
        return
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return
    pem, _ = ensure_keys()
    payload = json.dumps({"title": title, "body": body, "url": url})
    claims = {"sub": "mailto:e@mrg.im"}

    def run() -> None:
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=pem,
                    vapid_claims=claims,
                )
            except WebPushException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (404, 410):
                    db.delete_push_sub(user_id, sub["endpoint"])
            except Exception:
                continue

    threading.Thread(target=run, daemon=True).start()


def send_sync(user_id: int, title: str, body: str, url: str = "/purchases") -> int:
    from . import db

    if not db.get_notify(user_id):
        return 0
    return len(db.list_push_subs(user_id))
