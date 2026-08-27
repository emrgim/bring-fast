from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
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
        "id": "grandiose",
        "name": "Grandiose",
        "url": "https://www.grandiose.ae/",
        "logo": "/static/logos/grandiose.svg",
        "color": "#c9a227",
        "cart_url": "https://www.grandiose.ae/checkout/cart/",
        "checkout_url": "https://www.grandiose.ae/checkout/",
        "enabled": True,
        "search": True,
        "receipts": True,
        "login": True,
        "shop": True,
    },
    {
        "id": "unioncoop",
        "name": "Union Coop",
        "url": "https://www.unioncoop.ae/",
        "logo": "/static/logos/unioncoop.svg",
        "color": "#0b7a3e",
        "cart_url": "https://www.unioncoop.ae/checkout/cart/",
        "checkout_url": "https://www.unioncoop.ae/checkout/",
        "enabled": False,
        "search": True,
        "receipts": False,
        "login": False,
        "shop": True,
    },
    {
        "id": "carrefour",
        "name": "Carrefour UAE",
        "url": "https://www.carrefouruae.com/mafuae/en",
        "logo": "/static/logos/carrefour.svg",
        "color": "#004e9f",
        "cart_url": "https://www.carrefouruae.com/mafuae/en",
        "checkout_url": "https://www.carrefouruae.com/mafuae/en/cart",
        "enabled": False,
        "search": True,
        "receipts": True,
        "login": True,
        "shop": False,
    },
    {
        "id": "waitrose",
        "name": "Waitrose UAE",
        "url": "https://www.waitrose.ae/en/",
        "logo": "/static/logos/waitrose.svg",
        "color": "#007a33",
        "cart_url": "https://www.waitrose.ae/en/",
        "checkout_url": "https://www.waitrose.ae/en/checkout/",
        "enabled": False,
        "search": True,
        "receipts": False,
        "login": True,
        "shop": False,
    },
    {
        "id": "spinneys",
        "name": "Spinneys UAE",
        "url": "https://www.spinneys.com/en-ae/",
        "logo": "/static/logos/spinneys.svg",
        "color": "#8b1e3f",
        "cart_url": "https://www.spinneys.com/en-ae/",
        "checkout_url": "https://www.spinneys.com/en-ae/checkout/",
        "enabled": False,
        "search": True,
        "receipts": False,
        "login": True,
        "shop": False,
    },
    {
        "id": "mmi",
        "name": "MMI",
        "url": "https://www.mmihomedelivery.ae/",
        "logo": "/static/logos/mmi.svg",
        "color": "#1a1a1a",
        "cart_url": "https://www.mmihomedelivery.ae/",
        "checkout_url": "https://www.mmihomedelivery.ae/",
        "enabled": False,
        "search": True,
        "receipts": True,
        "login": True,
        "shop": False,
    },
    {
        "id": "africaneastern",
        "name": "African + Eastern",
        "url": "https://www.africaneasternonline.com/",
        "logo": "/static/logos/africaneastern.svg",
        "color": "#b11226",
        "cart_url": "https://www.africaneasternonline.com/",
        "checkout_url": "https://www.africaneasternonline.com/",
        "enabled": False,
        "search": True,
        "receipts": True,
        "login": True,
        "shop": False,
    },
    {
        # Food delivery, not a supermarket: the menu is per restaurant and per
        # hour, so there is no catalog to search and nothing to price against a
        # shelf. Careem is here to give its emailed invoices a store to land on.
        "id": "careem",
        "name": "Careem",
        "url": "https://www.careem.com/",
        "logo": "/static/logos/careem.svg",
        "color": "#3bb54a",
        "cart_url": "https://www.careem.com/",
        "checkout_url": "https://www.careem.com/",
        "enabled": False,
        "search": False,
        "receipts": True,
        "login": False,
        "shop": False,
    },
    {
        # Counter food, not a supermarket: the menu is per restaurant and
        # changes, so there is no catalog to search. McDonald's is here so
        # emailed order receipts have a store to land on.
        "id": "mcdonalds",
        "name": "McDonald's",
        "url": "https://www.mcdonalds.com/ae/en-ae.html",
        "logo": "/static/logos/mcdonalds.svg",
        "color": "#da291c",
        "cart_url": "https://www.mcdonalds.com/ae/en-ae.html",
        "checkout_url": "https://www.mcdonalds.com/ae/en-ae.html",
        "enabled": False,
        "search": False,
        "receipts": True,
        "login": False,
        "shop": False,
    },
]


def data_dir() -> Path:
    return Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast"))


def _fernet() -> Fernet:
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    key_file = root / "master.key"
    if not key_file.exists():
        key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(0o600)
    return Fernet(key_file.read_bytes())


_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    """Open the database. Schema work runs once per file, not on every query.

    Every page used to CREATE TABLE / PRAGMA table_info / seed store_flags on
    the way in — that is why a tab with a handful of SELECTs still felt slow.
    """
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "bringfast.db"
    key = str(path)
    con = sqlite3.connect(path, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    if key not in _schema_ready:
        with _schema_lock:
            if key not in _schema_ready:
                _init_schema(con)
                _schema_ready.add(key)
    return con


def _init_schema(con: sqlite3.Connection) -> None:
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
    con.execute(
        """CREATE TABLE IF NOT EXISTS store_flags (
            retailer TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL
        )"""
    )
    existing = {r[0] for r in con.execute("SELECT retailer FROM store_flags").fetchall()}
    for r in RETAILERS:
        if r["id"] not in existing:
            con.execute(
                "INSERT INTO store_flags(retailer, enabled) VALUES (?,?)",
                (r["id"], 1 if r.get("enabled") else 0),
            )
    con.execute(
        """CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            invoice_no TEXT NOT NULL,
            order_no TEXT,
            invoice_date TEXT,
            store_name TEXT,
            gmail_id TEXT,
            source_file TEXT,
            UNIQUE(user_id, retailer, invoice_no)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL,
            barcode TEXT,
            name TEXT NOT NULL,
            qty REAL NOT NULL,
            unit_price REAL,
            line_total REAL NOT NULL,
            image_url TEXT,
            product_key TEXT NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS product_meta (
            product_key TEXT PRIMARY KEY,
            sku TEXT,
            category TEXT,
            official_name TEXT,
            image_url TEXT,
            source TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS user_prefs (
            user_id INTEGER PRIMARY KEY,
            last_path TEXT,
            last_query TEXT,
            tab_queries TEXT
        )"""
    )
    prefs_cols = {r[1] for r in con.execute("PRAGMA table_info(user_prefs)").fetchall()}
    if "tab_queries" not in prefs_cols:
        con.execute("ALTER TABLE user_prefs ADD COLUMN tab_queries TEXT")
    con.execute(
        """CREATE TABLE IF NOT EXISTS catalog_prices (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            price REAL,
            found_name TEXT,
            sku TEXT,
            source TEXT,
            error TEXT,
            fetched_at TEXT NOT NULL,
            url TEXT
        )"""
    )
    cols = {r[1] for r in con.execute("PRAGMA table_info(catalog_prices)").fetchall()}
    if "url" not in cols:
        con.execute("ALTER TABLE catalog_prices ADD COLUMN url TEXT")
    meta_cols = {r[1] for r in con.execute("PRAGMA table_info(product_meta)").fetchall()}
    if "official_ean" not in meta_cols:
        con.execute("ALTER TABLE product_meta ADD COLUMN official_ean TEXT")
    con.execute(
        """CREATE TABLE IF NOT EXISTS product_aliases (
            alias_key TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS forecast_exclusions (
            user_id INTEGER NOT NULL,
            product_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, product_key)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS forecast_votes (
            user_id INTEGER NOT NULL,
            product_key TEXT NOT NULL,
            vote TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, product_key)
        )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_catalog_prices_lookup ON catalog_prices(user_id, product_key, retailer, fetched_at)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user_date ON invoices(user_id, invoice_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user_retailer ON invoices(user_id, retailer)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items(invoice_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_product ON invoice_items(product_key)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_product_aliases_canonical ON product_aliases(canonical_key)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_product_meta_ean ON product_meta(official_ean)")
    con.commit()


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


def set_password(user_id: int, password: str) -> None:
    if len(password or "") < 6:
        raise ValueError("password must be at least 6 characters")
    con = connect()
    con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
    con.commit()
    con.close()


def create_reset_token(email: str) -> str | None:
    user = get_user_by_email(email)
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    con = connect()
    con.execute(
        """CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    con.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
    con.execute(
        "INSERT INTO password_resets(token, user_id, created_at) VALUES (?,?,?)",
        (token, user["id"], int(time.time())),
    )
    con.commit()
    con.close()
    return token


def consume_reset_token(token: str, max_age: int = 3600) -> dict[str, Any] | None:
    if not token:
        return None
    con = connect()
    con.execute(
        """CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    row = con.execute("SELECT user_id, created_at FROM password_resets WHERE token=?", (token.strip(),)).fetchone()
    if not row:
        con.close()
        return None
    if int(time.time()) - int(row["created_at"]) > max_age:
        con.execute("DELETE FROM password_resets WHERE token=?", (token.strip(),))
        con.commit()
        con.close()
        return None
    con.execute("DELETE FROM password_resets WHERE token=?", (token.strip(),))
    con.commit()
    con.close()
    return get_user_by_id(row["user_id"])


def set_retailer_account(
    user_id: int, retailer: str, email: str, password: str = "", address: str = ""
) -> None:
    f = _fernet()
    con = connect()
    existing = con.execute(
        "SELECT secret, address FROM retailer_accounts WHERE user_id=? AND retailer=?",
        (user_id, retailer),
    ).fetchone()
    prev = {}
    if existing and existing["secret"]:
        try:
            prev = json.loads(f.decrypt(existing["secret"]).decode())
        except Exception:
            prev = {}
    if password:
        prev["email"] = email
        prev["password"] = password
    elif email:
        prev["email"] = email
        prev.setdefault("password", "")
    blob = f.encrypt(json.dumps(prev).encode()) if prev else (
        existing["secret"] if existing else f.encrypt(json.dumps({"email": email, "password": ""}).encode())
    )
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


def get_retailer_secret(user_id: int, retailer: str) -> dict[str, Any] | None:
    con = connect()
    row = con.execute(
        "SELECT email, secret, address FROM retailer_accounts WHERE user_id=? AND retailer=?",
        (user_id, retailer),
    ).fetchone()
    con.close()
    if not row:
        return None
    data = {"email": row["email"], "password": "", "address": row["address"] or "", "auth_token": "", "store_user_id": ""}
    if row["secret"]:
        try:
            payload = json.loads(_fernet().decrypt(row["secret"]).decode())
            data["email"] = payload.get("email") or data["email"]
            data["password"] = payload.get("password") or ""
            data["auth_token"] = payload.get("auth_token") or ""
            data["store_user_id"] = payload.get("store_user_id") or ""
        except Exception:
            pass
    return data


def save_store_session(user_id: int, retailer: str, *, token: str, store_user_id: str) -> None:
    """Keep the official-store API token next to the encrypted password."""
    current = get_retailer_secret(user_id, retailer) or {}
    email = current.get("email") or ""
    password = current.get("password") or ""
    f = _fernet()
    blob = f.encrypt(
        json.dumps(
            {
                "email": email,
                "password": password,
                "auth_token": token,
                "store_user_id": store_user_id,
            }
        ).encode()
    )
    con = connect()
    existing = con.execute(
        "SELECT address FROM retailer_accounts WHERE user_id=? AND retailer=?",
        (user_id, retailer),
    ).fetchone()
    addr = existing["address"] if existing else ""
    con.execute(
        """INSERT INTO retailer_accounts(user_id, retailer, email, secret, address)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, retailer) DO UPDATE SET secret=excluded.secret""",
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
        "SELECT retailer, email, address, secret FROM retailer_accounts WHERE user_id=?", (user_id,)
    ).fetchall()
    con.close()
    f = _fernet()
    linked = {r["retailer"]: r for r in rows}
    out = []
    for r in RETAILERS:
        row = linked.get(r["id"])
        password = ""
        if row is not None and row["secret"]:
            try:
                password = json.loads(f.decrypt(row["secret"]).decode()).get("password") or ""
            except Exception:
                password = ""
        out.append(
            {
                **r,
                "enabled": is_store_enabled(r["id"]),
                "linked": row is not None,
                "login_email": row["email"] if row else None,
                "has_password": bool(password),
                "delivery_address": (row["address"] if row else None) or "",
            }
        )
    return out


"""What a store can do here, in the order the cards read it out.

Search and price comparison read public catalog pages, so they go together and
a store without a catalog to read has neither. Cart and checkout are Magento
stores we have tested. Receipts is a parser for that store's invoices, and
login is a saved store account, which is what receipts and carts are read with.
A store can be receipts alone: its invoices are read, and nothing else.
"""
CAPABILITIES = [
    ("search", "Search"),
    ("compare", "Compare"),
    ("cart", "Cart"),
    ("checkout", "Checkout"),
    ("receipts", "Receipts"),
    ("login", "Login"),
]


def store_capabilities(retailer: str) -> list[dict[str, Any]]:
    for r in RETAILERS:
        if r["id"] != retailer:
            continue
        can = {
            "search": bool(r.get("search")),
            "compare": bool(r.get("search")),
            "cart": bool(r.get("shop")),
            "checkout": bool(r.get("shop")),
            "receipts": bool(r.get("receipts")),
            "login": bool(r.get("login")),
        }
        return [{"key": key, "label": label, "on": can[key]} for key, label in CAPABILITIES]
    return []


def store_meta(retailer: str) -> dict[str, Any] | None:
    for r in RETAILERS:
        if r["id"] == retailer:
            return {**r, "enabled": is_store_enabled(retailer)}
    return None


def is_store_enabled(retailer: str) -> bool:
    default = next((bool(r.get("enabled")) for r in RETAILERS if r["id"] == retailer), False)
    con = connect()
    row = con.execute("SELECT enabled FROM store_flags WHERE retailer=?", (retailer,)).fetchone()
    con.close()
    if row is None:
        return default
    return bool(row["enabled"])


def set_store_enabled(retailer: str, enabled: bool) -> None:
    if retailer not in {r["id"] for r in RETAILERS}:
        raise ValueError(f"unknown retailer {retailer}")
    con = connect()
    con.execute(
        """INSERT INTO store_flags(retailer, enabled) VALUES (?,?)
           ON CONFLICT(retailer) DO UPDATE SET enabled=excluded.enabled""",
        (retailer, 1 if enabled else 0),
    )
    con.commit()
    con.close()


def enabled_retailers() -> list[dict[str, Any]]:
    return [r for r in RETAILERS if is_store_enabled(r["id"])]


def store_can_shop(retailer: str) -> bool:
    """Cart/checkout only for Magento stores we tested: Grandiose and Union Coop."""
    return bool(next((r.get("shop") for r in RETAILERS if r["id"] == retailer), False))


def store_can_search(retailer: str) -> bool:
    """A store with a catalog to read. Receipts-only stores have none."""
    return bool(next((r.get("search") for r in RETAILERS if r["id"] == retailer), False))


def searchable_retailers() -> list[dict[str, Any]]:
    return [r for r in RETAILERS if r.get("search")]


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


def list_orders(user_id: int, retailer: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    con = connect()
    if retailer:
        rows = con.execute(
            """SELECT id, retailer, items_json, address, status, checkout_url, created_at
               FROM orders WHERE user_id=? AND retailer=? ORDER BY id DESC LIMIT ?""",
            (user_id, retailer, limit),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT id, retailer, items_json, address, status, checkout_url, created_at
               FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
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


def purge_local_copies() -> None:
    """Drop cached carts/orders. Only account links remain."""
    con = connect()
    con.execute("DELETE FROM carts")
    con.execute("DELETE FROM orders")
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


_TAB_PATHS = ("/dashboard", "/purchases")


def _tab_queries(row: sqlite3.Row | None) -> dict[str, str]:
    raw = (row["tab_queries"] if row else "") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v or "") for k, v in data.items()}


def set_last_view(user_id: int, path: str, query: str = "") -> bool:
    """Remember this tab's own query. A reload of the same view is a no-op."""
    con = connect()
    query = query or ""
    row = con.execute(
        "SELECT last_path, last_query, tab_queries FROM user_prefs WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if path in _TAB_PATHS:
        tabs = _tab_queries(row)
        tabs[path] = query
        blob = json.dumps(tabs)
        if (
            row
            and (row["last_path"] or "") == path
            and (row["last_query"] or "") == query
            and (row["tab_queries"] or "") == blob
        ):
            con.close()
            return False
        con.execute(
            """INSERT INTO user_prefs(user_id, last_path, last_query, tab_queries) VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 last_path=excluded.last_path,
                 last_query=excluded.last_query,
                 tab_queries=excluded.tab_queries""",
            (user_id, path, query, blob),
        )
    else:
        if row and (row["last_path"] or "") == path and (row["last_query"] or "") == query:
            con.close()
            return False
        con.execute(
            """INSERT INTO user_prefs(user_id, last_path, last_query) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET last_path=excluded.last_path, last_query=excluded.last_query""",
            (user_id, path, query),
        )
    con.commit()
    con.close()
    return True


def get_last_view(user_id: int) -> dict[str, str] | None:
    con = connect()
    row = con.execute("SELECT last_path, last_query FROM user_prefs WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return {"path": row["last_path"] or "", "query": row["last_query"] or ""} if row else None


def get_tab_query(user_id: int, path: str) -> str:
    """The query last left on this tab. Home and Buys do not share a window."""
    con = connect()
    row = con.execute("SELECT tab_queries FROM user_prefs WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    tabs = _tab_queries(row)
    own = tabs.get(path) or ""
    if own:
        return own
    # Older builds stored one shared window. Home may still restore from it;
    # Buys must not, or a dock tap applies Home's filters.
    if path == "/dashboard":
        return tabs.get("window") or ""
    return ""
