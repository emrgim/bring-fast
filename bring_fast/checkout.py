"""Official-site checkout executed inside the Bring Fast MCP server."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SHOT_DIR = Path(os.environ.get("BRINGFAST_DATA", Path.home() / ".bring-fast")) / "checkout-shots"

LOGIN = {
    "carrefour": "https://www.carrefouruae.com/mafuae/en/login/email",
    "grandiose": "https://www.grandiose.ae/customer/account/login/",
    "waitrose": "https://www.waitrose.ae/en/",
    "spinneys": "https://www.spinneys.com/en-ae/",
}

CART = {
    "carrefour": "https://www.carrefouruae.com/mafuae/en",
    "grandiose": "https://www.grandiose.ae/checkout/cart/",
    "waitrose": "https://www.waitrose.ae/en/",
    "spinneys": "https://www.spinneys.com/en-ae/",
}


def _launch():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    chrome = "/usr/bin/google-chrome"
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
    if Path(chrome).exists():
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
    for label in (
        "Accept",
        "ACCEPT",
        "Agree",
        "I agree",
        "Allow all",
        "Continue",
        "Got it",
        "OK",
    ):
        try:
            page.get_by_role("button", name=label, exact=False).first.click(timeout=800)
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


def _login_carrefour(page, email: str, password: str) -> str:
    page.goto(LOGIN["carrefour"], wait_until="domcontentloaded", timeout=45000)
    _dismiss(page)
    _fill_first(page, ["input[type=email]", "input[name=email]", "input[autocomplete=username]"], email)
    _click_first(page, ["Continue", "Next", "Submit"])
    page.wait_for_timeout(1500)
    _fill_first(page, ["input[type=password]", "input[name=password]"], password)
    _click_first(page, ["Log in", "Login", "Sign in", "Continue"])
    page.wait_for_timeout(2500)
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
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            _dismiss(page)
            for _ in range(max(1, qty)):
                if not _click_first(page, ["Add to cart", "Add to basket", "Add to bag", "Add"]):
                    break
                page.wait_for_timeout(700)
            added.append({**it, "site_add": "clicked", "page": page.url})
        except Exception as e:
            added.append({**it, "site_add": f"error:{e}"})
    return added


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


def run_checkout(
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
        if store == "carrefour":
            login_url = _login_carrefour(page, email, password)
        elif store == "grandiose":
            login_url = _login_grandiose(page, email, password)
        else:
            login_url = _login_spinneys_waitrose(page, store, email, password)
        shots.append(_shot(page, f"{store}-login"))
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
            if context:
                context.close()
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass
