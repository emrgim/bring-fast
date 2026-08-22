from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

import hashlib
import hmac

from cryptography.fernet import Fernet

DATA = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast"))
DB = DATA / "bringfast.db"
KEY_FILE = DATA / "master.key"

RETAILERS = [
    {
        "id": "carrefour",
        "name": "Carrefour UAE",
        "url": "https://www.carrefouruae.com/mafuae/en",
        "logo": "/static/logos/carrefour.svg",
        "color": "#004e9f",
        "cart_url": "https://www.carrefouruae.com/mafuae/en",
        "checkout_url": "https://www.carrefouruae.com/mafuae/en/cart",
    },
    {
        "id": "grandiose",
        "name": "Grandiose",
        "url": "https://www.grandiose.ae/",
        "logo": "/static/logos/grandiose.svg",
        "color": "#c9a227",
        "cart_url": "https://www.grandiose.ae/checkout/cart/",
        "checkout_url": "https://www.grandiose.ae/checkout/",
    },
    {
        "id": "waitrose",
        "name": "Waitrose UAE",
        "url": "https://www.waitrose.ae/en/",
        "logo": "/static/logos/waitrose.png",
        "color": "#007a33",
        "cart_url": "https://www.waitrose.ae/en/",
        "checkout_url": "https://www.waitrose.ae/en/checkout/",
    },
    {
        "id": "spinneys",
        "name": "Spinneys UAE",
        "url": "https://www.spinneys.com/en-ae/",
        "logo": "/static/logos/spinneys.svg",
        "color": "#8b1e3f",
        "cart_url": "https://www.spinneys.com/en-ae/",
        "checkout_url": "https://www.spinneys.com/en-ae/checkout/",
    },
]


def _fernet() -> Fernet:
    DATA.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(Fernet.generate_key())
        KEY_FILE.chmod(0o600)
    return Fernet(KEY_FILE.read_bytes())


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            mcp_token TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS retailer_accounts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            email TEXT,
            secret BLOB,
            UNIQUE(user_id, retailer)
        )"""
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(retailer_accounts)").fetchall()}
    if "address" not in cols:
        con.execute("ALTER TABLE retailer_accounts ADD COLUMN address TEXT")
    con.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            items_json TEXT NOT NULL,
            address TEXT,
            status TEXT NOT NULL,
            checkout_url TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS carts (
            user_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            items_json TEXT NOT NULL,
            PRIMARY KEY (user_id, retailer)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS oauth_codes (
            code TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            redirect_uri TEXT,
            created_at INTEGER NOT NULL,
            client_id TEXT,
            code_challenge TEXT,
            code_challenge_method TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id TEXT PRIMARY KEY,
            client_secret TEXT,
            redirect_uris TEXT NOT NULL,
            token_endpoint_auth_method TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(oauth_codes)").fetchall()}
    for col, typ in (
        ("client_id", "TEXT"),
        ("code_challenge", "TEXT"),
        ("code_challenge_method", "TEXT"),
    ):
        if col not in cols:
            con.execute(f"ALTER TABLE oauth_codes ADD COLUMN {col} {typ}")
    con.commit()
    return con


def create_user(email: str, password: str) -> dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email or len(password) < 6:
        raise ValueError("email and password (min 6) required")
    token = secrets.token_urlsafe(32)
    con = connect()
    try:
        con.execute(
            "INSERT INTO users(email, password_hash, mcp_token, created_at) VALUES (?,?,?,?)",
            (email, hash_password(password), token, int(time.time())),
        )
        con.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError("email already registered") from e
    finally:
        con.close()
    return get_user_by_email(email)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    con = connect()
    row = con.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    con.close()
    return dict(row) if row else None


def get_user_by_id(uid: int) -> dict[str, Any] | None:
    con = connect()
    row = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_user_by_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    con = connect()
    row = con.execute("SELECT * FROM users WHERE mcp_token=?", (token.strip(),)).fetchone()
    con.close()
    return dict(row) if row else None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(user: dict[str, Any], password: str) -> bool:
    try:
        kind, salt, digest = str(user["password_hash"]).split("$", 2)
        if kind != "pbkdf2":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def rotate_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    con = connect()
    con.execute("UPDATE users SET mcp_token=? WHERE id=?", (token, user_id))
    con.commit()
    con.close()
    return token


def set_retailer_account(
    user_id: int, retailer: str, email: str, password: str = "", address: str = ""
) -> None:
    f = _fernet()
    con = connect()
    existing = con.execute(
        "SELECT secret, address FROM retailer_accounts WHERE user_id=? AND retailer=?",
        (user_id, retailer),
    ).fetchone()
    if password:
        blob = f.encrypt(json.dumps({"email": email, "password": password}).encode())
    elif existing:
        blob = existing["secret"]
    else:
        blob = f.encrypt(json.dumps({"email": email, "password": ""}).encode())
    addr = (address or "").strip() or (existing["address"] if existing else "")
    con.execute(
        """INSERT INTO retailer_accounts(user_id, retailer, email, secret, address)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, retailer) DO UPDATE SET
             email=excluded.email, secret=excluded.secret, address=excluded.address""",
        (user_id, retailer, email, blob, addr),
    )
    con.commit()
    con.close()


def clear_retailer_account(user_id: int, retailer: str) -> None:
    con = connect()
    con.execute("DELETE FROM retailer_accounts WHERE user_id=? AND retailer=?", (user_id, retailer))
    con.commit()
    con.close()


def list_retailer_accounts(user_id: int) -> list[dict[str, Any]]:
    con = connect()
    rows = con.execute(
        "SELECT retailer, email, address FROM retailer_accounts WHERE user_id=?", (user_id,)
    ).fetchall()
    con.close()
    linked = {r["retailer"]: r for r in rows}
    out = []
    for r in RETAILERS:
        row = linked.get(r["id"])
        out.append(
            {
                **r,
                "linked": row is not None,
                "login_email": row["email"] if row else None,
                "delivery_address": (row["address"] if row else None) or "",
            }
        )
    return out


def store_meta(retailer: str) -> dict[str, Any] | None:
    for r in RETAILERS:
        if r["id"] == retailer:
            return r
    return None


def create_order(user_id: int, retailer: str, items: list, address: str, checkout_url: str) -> dict[str, Any]:
    con = connect()
    cur = con.execute(
        """INSERT INTO orders(user_id, retailer, items_json, address, status, checkout_url, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (user_id, retailer, json.dumps(items), address, "awaiting_official_payment", checkout_url, int(time.time())),
    )
    oid = cur.lastrowid
    con.commit()
    con.close()
    return {
        "order_id": oid,
        "retailer": retailer,
        "items": items,
        "delivery_address": address,
        "status": "awaiting_official_payment",
        "checkout_url": checkout_url,
    }


def list_orders(user_id: int, retailer: str, limit: int = 5) -> list[dict[str, Any]]:
    con = connect()
    rows = con.execute(
        """SELECT id, retailer, items_json, address, status, checkout_url, created_at
           FROM orders WHERE user_id=? AND retailer=? ORDER BY id DESC LIMIT ?""",
        (user_id, retailer, limit),
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        out.append(
            {
                "order_id": r["id"],
                "retailer": r["retailer"],
                "items": json.loads(r["items_json"]),
                "delivery_address": r["address"],
                "status": r["status"],
                "checkout_url": r["checkout_url"],
                "created_at": r["created_at"],
            }
        )
    return out


def load_cart(user_id: int, retailer: str) -> dict[str, Any]:
    con = connect()
    row = con.execute(
        "SELECT items_json FROM carts WHERE user_id=? AND retailer=?", (user_id, retailer)
    ).fetchone()
    con.close()
    if not row:
        return {"items": [], "currency": "AED"}
    return json.loads(row["items_json"])


def save_cart(user_id: int, retailer: str, cart: dict[str, Any]) -> None:
    con = connect()
    con.execute(
        """INSERT INTO carts(user_id, retailer, items_json) VALUES (?,?,?)
           ON CONFLICT(user_id, retailer) DO UPDATE SET items_json=excluded.items_json""",
        (user_id, retailer, json.dumps(cart)),
    )
    con.commit()
    con.close()


def save_oauth_code(
    user_id: int,
    redirect_uri: str,
    client_id: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
) -> str:
    code = secrets.token_urlsafe(24)
    con = connect()
    con.execute(
        """INSERT INTO oauth_codes(code, user_id, redirect_uri, created_at, client_id, code_challenge, code_challenge_method)
           VALUES (?,?,?,?,?,?,?)""",
        (code, user_id, redirect_uri, int(time.time()), client_id, code_challenge, code_challenge_method),
    )
    con.commit()
    con.close()
    return code


def consume_oauth_code(code: str, redirect_uri: str | None = None) -> dict[str, Any] | None:
    con = connect()
    row = con.execute("SELECT * FROM oauth_codes WHERE code=?", (code,)).fetchone()
    if not row:
        con.close()
        return None
    if int(time.time()) - int(row["created_at"]) > 300:
        con.execute("DELETE FROM oauth_codes WHERE code=?", (code,))
        con.commit()
        con.close()
        return None
    if redirect_uri and row["redirect_uri"] and row["redirect_uri"] != redirect_uri:
        con.close()
        return None
    user = con.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    con.execute("DELETE FROM oauth_codes WHERE code=?", (code,))
    con.commit()
    con.close()
    if not user:
        return None
    out = dict(user)
    out["_oauth_client_id"] = row["client_id"]
    out["_oauth_code_challenge"] = row["code_challenge"]
    out["_oauth_code_challenge_method"] = row["code_challenge_method"]
    out["_oauth_redirect_uri"] = row["redirect_uri"]
    return out


def register_oauth_client(redirect_uris: list[str], token_endpoint_auth_method: str = "none") -> dict[str, Any]:
    client_id = "fb_" + secrets.token_urlsafe(16)
    con = connect()
    con.execute(
        """INSERT INTO oauth_clients(client_id, client_secret, redirect_uris, token_endpoint_auth_method, created_at)
           VALUES (?,?,?,?,?)""",
        (client_id, None, json.dumps(redirect_uris), token_endpoint_auth_method or "none", int(time.time())),
    )
    con.commit()
    con.close()
    return {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": token_endpoint_auth_method or "none",
        "client_id_issued_at": int(time.time()),
    }


def get_oauth_client(client_id: str) -> dict[str, Any] | None:
    if not client_id:
        return None
    if client_id == "fast-bring":
        return {
            "client_id": "fast-bring",
            "redirect_uris": None,
            "token_endpoint_auth_method": "none",
        }
    con = connect()
    row = con.execute("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)).fetchone()
    con.close()
    return dict(row) if row else None
