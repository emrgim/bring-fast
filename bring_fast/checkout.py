"""Official-site checkout executed inside the Bring Fast MCP server."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SHOT_DIR = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast")) / "checkout-shots"

LOGIN = {
    "carrefour": "https://www.carrefouruae.com/mafuae/en/login/email",
    "grandiose": "https://www.grandiose.ae/customer/account/login/",
    "waitrose": "https://www.waitrose.ae/en/",
    "spinneys": "https://www.spinneys.com/en-ae/",
    "mmi": "https://www.mmihomedelivery.ae/customer/account/login",
    "africaneastern": "https://www.africaneasternonline.com/login",
}

HOME = {
    "carrefour": "https://www.carrefouruae.com/mafuae/en",
    "grandiose": "https://www.grandiose.ae/",
    "waitrose": "https://www.waitrose.ae/en/",
    "spinneys": "https://www.spinneys.com/en-ae/",
    "mmi": "https://www.mmihomedelivery.ae/",
    "africaneastern": "https://www.africaneasternonline.com/",
}

# Bring Fast is multi-user but the desktop Chrome profile is shared, so a store
# session has to be tied to the account that opened it before it can be reused.
ACCOUNT_MARKER = "bringfast_account"

SIGNED_OUT_MARKERS = (
    "log in or sign up",
    "login or sign up",
    "sign in or register",
    "create an account",
    "forgot password",
)

CART = {
    "carrefour": "https://www.carrefouruae.com/mafuae/en",
    "grandiose": "https://www.grandiose.ae/checkout/cart/",
    "waitrose": "https://www.waitrose.ae/en/",
    "spinneys": "https://www.spinneys.com/en-ae/",
}


CDP = os.environ.get("BRINGFAST_CDP", "http://127.0.0.1:9222")
DESK_PROFILE = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast")) / "chrome-desktop"


def _cdp_port(cdp: str) -> int:
    parsed = urlsplit(cdp)
    return parsed.port or 9222


def _chrome_bin() -> str | None:
    candidates = []
    if os.environ.get("BRINGFAST_CHROME"):
        candidates.append(os.environ["BRINGFAST_CHROME"])
    candidates.extend(
        ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome-stable"]
    )
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _desktop_env() -> dict[str, str]:
    import glob

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    if not env.get("XAUTHORITY"):
        found = sorted(
            glob.glob(f"{runtime}/.mutter-Xwaylandauth.*") + glob.glob("/run/user/*/.mutter-Xwaylandauth.*"),
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        home_auth = os.path.expanduser("~/.Xauthority")
        if found:
            env["XAUTHORITY"] = found[0]
        elif os.path.exists(home_auth):
            env["XAUTHORITY"] = home_auth
    return env


def _ensure_desktop_chrome() -> None:
    import subprocess
    import urllib.request

    try:
        urllib.request.urlopen(f"{CDP}/json/version", timeout=1)
        return
    except Exception:
        pass
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return
    chrome = _chrome_bin()
    if not chrome:
        return
    env = _desktop_env()
    DESK_PROFILE.mkdir(parents=True, exist_ok=True)
    log = DESK_PROFILE.parent / "chrome-desktop.log"
    subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={DESK_PROFILE}",
            f"--remote-debugging-port={_cdp_port(CDP)}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--start-maximized",
        ],
        stdout=open(log, "ab"),
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    for _ in range(20):
        time.sleep(0.4)
        try:
            urllib.request.urlopen(f"{CDP}/json/version", timeout=1)
            return
        except Exception:
            continue


def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Install checkout support with "
            "`pip install 'bring-fast[checkout]'` and `playwright install chromium`."
        ) from e

    pw = sync_playwright().start()
    try:
        _ensure_desktop_chrome()
        browser = pw.chromium.connect_over_cdp(CDP)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return pw, browser, context
    except Exception:
        chrome = _chrome_bin()
        kwargs = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if chrome:
            kwargs["executable_path"] = chrome
        browser = pw.chromium.launch(**kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-AE",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        return pw, browser, context


def _shot(page, name: str) -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{int(time.time())}-{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return ""


def _dismiss(page) -> None:
    for sel in (
        "button:has-text('Select All')",
        "button:has-text('Save Changes')",
        "#cmpCloseBtn",
        "button#cmpCloseBtn",
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        ".onetrust-accept-btn-handler",
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept all cookies')",
        "button:has-text('Allow all')",
        "button:has-text('Accept All')",
        "button:has-text('I Accept')",
        "#onetrust-pc-btn-handler",
    ):
        try:
            page.locator(sel).first.click(timeout=1200)
            page.wait_for_timeout(400)
        except Exception:
            pass
    for label in (
        "Accept All Cookies",
        "Accept all",
        "Accept",
        "ACCEPT",
        "Agree",
        "I agree",
        "Allow all",
        "Confirm My Choices",
        "Got it",
        "OK",
    ):
        try:
            page.get_by_role("button", name=label, exact=False).first.click(timeout=800)
        except Exception:
            pass
    try:
        page.evaluate(
            """() => {
              const b = document.querySelector('#onetrust-accept-btn-handler');
              if (b) b.click();
              document.querySelector('#onetrust-banner-sdk')?.remove();
              document.querySelector('#onetrust-pc-sdk')?.remove();
              document.querySelector('.onetrust-pc-dark-filter')?.remove();
              document.querySelectorAll('.cmp-body, .f-pref-sdk-banner, .cmp-theme, .show-modal, #_next-securiti-ai, #_next-securiti-ai-gc').forEach(e => e.remove());
              document.body.style.overflow = 'auto';
              document.body.style.pointerEvents = 'auto';
            }"""
        )
    except Exception:
        pass


def _fill_first(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.fill(value, timeout=2500)
                return True
        except Exception:
            continue
    return False


def _click_first(page, names: list[str]) -> bool:
    for name in names:
        try:
            page.get_by_role("button", name=name, exact=False).first.click(timeout=2000)
            return True
        except Exception:
            try:
                page.get_by_text(name, exact=False).first.click(timeout=1500)
                return True
            except Exception:
                continue
    return False


def _marked_account(page) -> str:
    """Which Bring Fast store account this browser profile last signed in as."""
    try:
        return (page.evaluate(f"() => localStorage.getItem({ACCOUNT_MARKER!r}) || ''") or "").strip().lower()
    except Exception:
        return ""


def _mark_account(page, email: str) -> None:
    try:
        page.evaluate(f"(v) => localStorage.setItem({ACCOUNT_MARKER!r}, v)", (email or "").strip().lower())
    except Exception:
        pass


def _looks_signed_out(page) -> bool:
    try:
        text = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(marker in text for marker in SIGNED_OUT_MARKERS)


def _drop_store_session(page, store: str) -> None:
    """Wipe the store session so the next login cannot inherit another account."""
    try:
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
    except Exception:
        pass
    context = getattr(page, "context", None)
    if context is None:
        return
    host = urlsplit(HOME.get(store, "")).hostname or ""
    domain = ".".join(host.split(".")[-2:]) if host else ""
    if domain:
        try:
            context.clear_cookies(domain=domain)
            return
        except Exception:
            pass
    try:
        context.clear_cookies()
    except Exception:
        pass


def _session_state(page, email: str) -> str:
    """"mine" when this profile is signed in as `email`, else "other" or "anonymous"."""
    marked = _marked_account(page)
    if _looks_signed_out(page):
        return "anonymous"
    if marked and marked == (email or "").strip().lower():
        return "mine"
    return "other" if marked else "anonymous"


def _reuse_session(page, store: str, email: str) -> bool:
    """Reuse a live store session instead of logging in again on every tool call."""
    page.goto(HOME.get(store, LOGIN[store]), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1200)
    _dismiss(page)
    state = _session_state(page, email)
    if state == "mine":
        return True
    if state == "other":
        _drop_store_session(page, store)
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        _dismiss(page)
    return False


def _login_carrefour(page, email: str, password: str) -> str:
    from urllib.parse import quote

    url = (
        "https://www.carrefouruae.com/mafuae/en/login/email/password"
        f"?email={quote(email)}&hasPassword=true&hasOtpEmail=true"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    _dismiss(page)
    try:
        page.locator("a.cc-btn.cc-dismiss").click(timeout=2500)
    except Exception:
        pass
    page.wait_for_selector("input[type=password]", timeout=15000)
    pwd = page.locator("input[type=password]").first
    pwd.click()
    pwd.press_sequentially(password, delay=40)
    try:
        page.locator("button:has-text('Login')").click(timeout=5000)
    except Exception:
        pwd.press("Enter")
    page.wait_for_timeout(4000)
    return page.url


def _login_grandiose(page, email: str, password: str) -> str:
    page.goto(LOGIN["grandiose"], wait_until="domcontentloaded", timeout=45000)
    _dismiss(page)
    _fill_first(page, ["input#email", "input[name=login[username]]", "input[type=email]"], email)
    _fill_first(page, ["input#pass", "input[name=login[password]]", "input[type=password]"], password)
    _click_first(page, ["Sign In", "Login", "Log in"])
    page.wait_for_timeout(2500)
    return page.url


def _login_spinneys_waitrose(page, store: str, email: str, password: str) -> str:
    page.goto(LOGIN[store], wait_until="domcontentloaded", timeout=45000)
    _dismiss(page)
    _click_first(page, ["Sign in", "Log in", "Login", "Account"])
    page.wait_for_timeout(800)
    _fill_first(page, ["input[type=email]", "input[name=email]", "input[name=username]"], email)
    _fill_first(page, ["input[type=password]", "input[name=password]"], password)
    _click_first(page, ["Sign in", "Log in", "Login"])
    page.wait_for_timeout(2500)
    return page.url


def ensure_store_login(page, store: str, email: str, password: str) -> dict[str, Any]:
    """Get this page to a session that belongs to `email`, reusing one when possible."""
    wanted = (email or "").strip().lower()
    if not wanted or not password:
        return {
            "logged_in": False,
            "reused": False,
            "final_url": "",
            "error": f"No {store} login saved. Add the store email and password on the Bring Fast dashboard.",
        }
    if _reuse_session(page, store, wanted):
        return {"logged_in": True, "reused": True, "final_url": page.url, "error": None}
    try:
        if store == "carrefour":
            _login_carrefour(page, email, password)
        elif store == "grandiose":
            _login_grandiose(page, email, password)
        else:
            _login_spinneys_waitrose(page, store, email, password)
    except Exception as e:
        return {
            "logged_in": False,
            "reused": False,
            "final_url": getattr(page, "url", ""),
            "error": (
                f"The {store} sign-in page did not accept the saved login for {email} ({e}). "
                "Check the store email and password on the Bring Fast dashboard."
            ),
        }
    if "login" in (page.url or "").lower():
        page.wait_for_timeout(2000)
    ok = "login" not in (page.url or "").lower() and not _looks_signed_out(page)
    if ok:
        _mark_account(page, wanted)
    return {
        "logged_in": ok,
        "reused": False,
        "final_url": page.url,
        "error": None
        if ok
        else (
            f"Could not sign in to {store} as {email}. Check the store password on the Bring Fast dashboard; "
            "the store may also be asking for a one-time code."
        ),
    }


def _add_items(page, store: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    added = []
    for it in items:
        pid = str(it.get("id") or "")
        qty = int(it.get("qty") or 1)
        url = it.get("url") or ""
        if store == "carrefour" and pid:
            url = url or f"https://www.carrefouruae.com/mafuae/en/p/{pid}"
        if not url:
            added.append({**it, "site_add": "skipped_no_url"})
            continue
        try:
            page.goto(url, wait_until="commit", timeout=40000)
            page.wait_for_timeout(2500)
            _dismiss(page)
            clicked = 0
            for _ in range(max(1, qty)):
                if not _click_first(page, ["Add to cart", "Add to basket", "Add to bag"]):
                    break
                clicked += 1
                page.wait_for_timeout(1200)
            added.append({**it, "site_add": "clicked" if clicked else "no_button", "page": page.url})
        except Exception as e:
            added.append({**it, "site_add": f"error:{e}"})
    return added


def _open_carrefour_cart(page) -> None:
    page.goto("https://www.carrefouruae.com/mafuae/en", wait_until="commit", timeout=45000)
    page.wait_for_timeout(2500)
    _dismiss(page)
    try:
        page.evaluate(
            """() => {
              const els = [].slice.call(document.querySelectorAll('a,button'));
              const c = els.find(e => /cart/i.test(e.getAttribute('aria-label')||''))
                || els.find(e => /^\\d+$/.test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 4);
              if (c) c.click();
            }"""
        )
        page.wait_for_timeout(3000)
    except Exception:
        pass
    if "cart" not in (page.url or ""):
        try:
            page.goto("https://www.carrefouruae.com/mafuae/en/app/cart", wait_until="commit", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception:
            pass


def _scrape_carrefour_cart(page) -> list[dict[str, Any]]:
    text = ""
    try:
        text = page.inner_text("body") or ""
    except Exception:
        return []
    chunk = text
    if "My Cart" in text:
        chunk = text.split("My Cart", 1)[1]
        for stop in ("Subtotal", "Order Summary", "Ready to Checkout"):
            if stop in chunk:
                chunk = chunk.split(stop, 1)[0]
                break
    names = []
    skip = {
        "delete all",
        "low prices",
        "my cart",
        "checkout",
        "switch",
        "start",
        "min. value",
        "free delivery",
        "add extra services",
        "allow grocery substitutions",
        "pack my order",
    }
    for line in chunk.splitlines():
        line = line.strip()
        if len(line) < 10 or line.lower() in skip:
            continue
        if line.startswith("AED") or line.replace(".", "", 1).isdigit():
            continue
        if not any(ch.isalpha() for ch in line):
            continue
        if line.lower().startswith("add "):
            continue
        if line not in names:
            names.append(line)
    hrefs = {}
    try:
        hrefs = page.evaluate(
            """() => {
              const m = {};
              document.querySelectorAll('a[href*="/p/"]').forEach(a => {
                const n = (a.innerText||'').trim();
                if (n) m[n] = a.href;
              });
              return m;
            }"""
        ) or {}
    except Exception:
        hrefs = {}
    out = []
    for name in names[:20]:
        url = hrefs.get(name) or ""
        pid = ""
        if "/p/" in url:
            pid = url.rsplit("/p/", 1)[-1].split("?")[0]
        out.append({"id": pid, "name": name, "url": url, "qty": 1, "currency": "AED"})
    return out


def _goto_checkout(page, store: str) -> str:
    if store == "carrefour":
        for name in ("Cart", "Basket", "View cart", "Checkout", "Proceed"):
            _click_first(page, [name])
            page.wait_for_timeout(800)
        if "cart" not in page.url and "checkout" not in page.url:
            page.goto("https://www.carrefouruae.com/mafuae/en", wait_until="domcontentloaded", timeout=40000)
            _dismiss(page)
            _click_first(page, ["Cart", "Basket", "Checkout"])
            page.wait_for_timeout(1500)
        _click_first(page, ["Checkout", "Proceed to checkout", "Place order"])
        page.wait_for_timeout(2000)
        return page.url
    page.goto(CART[store], wait_until="domcontentloaded", timeout=40000)
    _dismiss(page)
    _click_first(page, ["Checkout", "Proceed to checkout", "Place order", "Continue to checkout", "Go to cart"])
    page.wait_for_timeout(2000)
    return page.url


class LiveCartTimeout(TimeoutError):
    """Raised when official-site browser work exceeds the MCP-safe budget."""


def _in_thread(fn, timeout: float = 240, **kwargs):
    import concurrent.futures

    # Do not use `with ThreadPoolExecutor`: on timeout its shutdown(wait=True)
    # keeps the MCP request blocked until Chrome finishes (often > Grok's limit).
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn, **kwargs).result(timeout=timeout)
    except concurrent.futures.TimeoutError as e:
        store = kwargs.get("store") or "store"
        raise LiveCartTimeout(
            f"Live {store} cart/status exceeded {int(timeout)}s while driving the official site. "
            "whoami/stores stay fast because they only read Bring Fast; cart/status open Chrome."
        ) from e
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def verify_login(*, store: str, email: str, password: str) -> dict[str, Any]:
    if store == "carrefour":
        from bring_fast.stores import carrefour as carrefour_api

        auth = carrefour_api.login(email, password)
        return {
            "ok": bool(auth.get("ok")),
            "reused": False,
            "url": "",
            "error": auth.get("error"),
            "driver": "http",
        }
    if store == "grandiose":
        from bring_fast.stores import grandiose as grandiose_api

        auth = grandiose_api.login(email, password)
        return {
            "ok": bool(auth.get("ok")),
            "reused": False,
            "url": "",
            "error": auth.get("error"),
            "driver": "http",
        }
    if store == "mmi":
        from bring_fast.stores import mmi as mmi_api

        auth = mmi_api.login(email, password)
        return {
            "ok": bool(auth.get("ok")),
            "reused": False,
            "url": "",
            "error": auth.get("error"),
            "driver": "http",
        }
    if store == "africaneastern":
        from bring_fast.stores import africaneastern as ae_api

        auth = ae_api.login(email, password)
        return {
            "ok": bool(auth.get("ok")),
            "reused": False,
            "url": "",
            "error": auth.get("error"),
            "driver": "http",
        }
    return {
        "ok": False,
        "error": f"{store} HTTP login is not wired yet. Chrome will not be used.",
        "driver": "http",
    }


def _verify_login_sync(*, store: str, email: str, password: str) -> dict[str, Any]:
    if store not in LOGIN:
        return {"ok": False, "error": f"Unknown store {store}."}
    if not email or not password:
        return {
            "ok": False,
            "error": "Save the store email and password first, then check the login again.",
        }
    pw = browser = context = None
    try:
        pw, browser, context = _launch()
        page = context.new_page()
        session = ensure_store_login(page, store, email, password)
        return {
            "ok": bool(session["logged_in"]),
            "reused": bool(session.get("reused")),
            "url": session.get("final_url"),
            "error": session.get("error"),
        }
    except Exception as e:
        return {"ok": False, "error": f"Could not open a browser to check the login: {e}"}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def official_cart(
    *,
    store: str,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
    timeout: float = 25,
    session_token: str = "",
    session_user: str = "",
) -> dict[str, Any]:
    """Official store cart via HTTP APIs. No real Chrome window."""
    if store == "carrefour":
        from bring_fast.stores import carrefour as carrefour_api

        return carrefour_api.official_cart(
            email=email,
            password=password,
            action=action,
            items=items,
            session_token=session_token,
            session_user=session_user,
        )
    if store == "grandiose":
        from bring_fast.stores import grandiose as grandiose_api

        return grandiose_api.official_cart(
            email=email,
            password=password,
            action=action,
            items=items,
            session_token=session_token,
            session_user=session_user,
        )
    return {
        "ok": False,
        "official_count": None,
        "items": [],
        "logged_in": False,
        "session_reused": False,
        "driver": "http",
        "error": f"{store} HTTP API client is not wired yet. Chrome will not be used.",
    }


def _official_cart_sync(
    *,
    store: str,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Login on the official site and mutate the real cart. Returns official_count."""
    if not email or not password:
        return {
            "ok": False,
            "official_count": None,
            "logged_in": False,
            "error": f"No {store} login saved. Add the store email and password on the Bring Fast dashboard.",
        }
    pw = browser = context = None
    try:
        pw, browser, context = _launch()
        page = context.new_page()
        session = ensure_store_login(page, store, email, password)
        logged = bool(session["logged_in"])
        if not logged:
            return {
                "ok": False,
                "official_count": None,
                "items": [],
                "logged_in": False,
                "session_reused": False,
                "final_url": session.get("final_url") or page.url,
                "error": f"{session['error']} Store cart was not changed.",
            }
        if store == "carrefour":
            if action in ("add", "set") and items:
                _add_items(page, store, items)
            elif action == "clear":
                _open_carrefour_cart(page)
                _click_first(page, ["Delete All", "Clear cart", "Remove all"])
                page.wait_for_timeout(1500)
            elif action == "remove" and items:
                _open_carrefour_cart(page)
                for it in items:
                    name = it.get("name") or ""
                    if name:
                        try:
                            page.get_by_text(name, exact=False).first.locator("xpath=ancestor::*[.//button][1]").get_by_text("Remove", exact=False).click(timeout=2000)
                        except Exception:
                            pass
            _open_carrefour_cart(page)
            live_items = _scrape_carrefour_cart(page)
            count = len(live_items)
            ok = True
            return {
                "ok": ok,
                "official_count": count,
                "items": live_items,
                "logged_in": True,
                "session_reused": bool(session.get("reused")),
                "final_url": page.url,
                "error": None,
            }
        else:
            api = {"tries": [], "note": "non-carrefour uses click path"}
            if action in ("add", "set"):
                _add_items(page, store, items)
        count = _extract_count(api if store == "carrefour" else None, page)
        live_items = _extract_items(api if isinstance(api, dict) else None)
        ok = logged and (
            action in ("list", "clear", "remove") or (count or 0) > 0 or bool(live_items)
        )
        return {
            "ok": ok,
            "official_count": count if count is not None else len(live_items),
            "items": live_items,
            "logged_in": logged,
            "session_reused": bool(session.get("reused")),
            "final_url": page.url,
            "error": None if ok else "Store cart unavailable after login.",
        }
    except Exception as e:
        return {"ok": False, "official_count": None, "error": str(e)}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def _extract_items(api) -> list[dict[str, Any]]:
    if not isinstance(api, dict):
        return []
    for blob in (api.get("lite"), api.get("v8")):
        obj = blob
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                continue
        if not isinstance(obj, dict):
            continue
        data = obj.get("data") or obj.get("cart") or obj.get("basket") or obj
        items = data.get("items") or data.get("products") or data.get("entries") or []
        if isinstance(items, list) and items:
            out = []
            for i in items:
                if not isinstance(i, dict):
                    continue
                out.append(
                    {
                        "id": str(i.get("id") or i.get("productId") or i.get("sku") or ""),
                        "name": i.get("name") or i.get("title") or "",
                        "qty": int(i.get("qty") or i.get("quantity") or 1),
                        "price": i.get("price") or i.get("unitPrice"),
                    }
                )
            return out
    return []


def _extract_count(api, page) -> int | None:
    if isinstance(api, dict):
        for blob in (api.get("lite"), api.get("v8")):
            n = _count_from_obj(blob)
            if n is not None:
                return n
    try:
        text = page.inner_text("body") or ""
        import re

        m = re.search(r"(\d+)\s+items? in (?:your )?cart", text, re.I)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _count_from_obj(obj) -> int | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    for key in ("totalQuantity", "quantity", "cartCount", "itemCount", "count"):
        if isinstance(obj.get(key), (int, float)):
            return int(obj[key])
    data = obj.get("data") or obj.get("cart") or obj.get("basket") or {}
    if isinstance(data, dict):
        for key in ("totalQuantity", "quantity", "cartCount", "itemCount"):
            if isinstance(data.get(key), (int, float)):
                return int(data[key])
        items = data.get("items") or data.get("products") or data.get("entries")
        if isinstance(items, list):
            return sum(int(i.get("qty") or i.get("quantity") or 1) for i in items if isinstance(i, dict))
    items = obj.get("items") or obj.get("products")
    if isinstance(items, list):
        return len(items)
    return None


def run_checkout(
    *,
    store: str,
    email: str,
    password: str,
    address: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if store == "grandiose":
        from bring_fast.stores import grandiose as grandiose_api

        live = grandiose_api.official_checkout(email=email, password=password)
        return live
    return _in_thread(
        _run_checkout_sync,
        store=store,
        email=email,
        password=password,
        address=address,
        items=items,
    )


def _run_checkout_sync(
    *,
    store: str,
    email: str,
    password: str,
    address: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    if not email or not password:
        return {
            "ok": False,
            "stage": "credentials",
            "error": "Store login missing. Save email+password on the Bring Fast dashboard for this store.",
        }
    if not items:
        return {"ok": False, "stage": "cart", "error": "Cart empty"}

    pw = browser = context = None
    shots: list[str] = []
    try:
        pw, browser, context = _launch()
        page = context.new_page()
        session = ensure_store_login(page, store, email, password)
        shots.append(_shot(page, f"{store}-login"))
        if not session["logged_in"]:
            return {
                "ok": False,
                "stage": "login",
                "login_url": session.get("final_url") or LOGIN.get(store),
                "error": f"{session['error']} Nothing was ordered.",
                "screenshots": [s for s in shots if s],
            }
        login_url = session.get("final_url") or page.url
        added = _add_items(page, store, items)
        shots.append(_shot(page, f"{store}-added"))
        checkout_url = _goto_checkout(page, store)
        shots.append(_shot(page, f"{store}-checkout"))
        text = ""
        try:
            text = page.inner_text("body")[:1500]
        except Exception:
            pass
        paid = any(k in text.lower() for k in ("order confirmed", "thank you", "order number", "placed successfully"))
        at_pay = any(k in text.lower() for k in ("card number", "payment", "pay now", "place order", "cvv"))
        return {
            "ok": True,
            "stage": "confirmed" if paid else ("payment" if at_pay else "checkout"),
            "login_url": login_url,
            "checkout_url": checkout_url,
            "final_url": page.url,
            "delivery_address": address,
            "session_reused": bool(session.get("reused")),
            "items_on_site": added,
            "page_excerpt": text[:500],
            "screenshots": [s for s in shots if s],
            "payment_completed": paid,
            "what_happens": (
                "Official order confirmed on the supermarket site."
                if paid
                else (
                    f"Logged into {store} on the MCP server, added items, opened checkout at {page.url}. "
                    "Payment/3DS still on the official page (card is not stored in Bring Fast)."
                    if at_pay
                    else f"Logged into {store} and pushed the cart to official checkout: {page.url}."
                )
            ),
        }
    except Exception as e:
        return {"ok": False, "stage": "browser", "error": str(e), "screenshots": shots}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
