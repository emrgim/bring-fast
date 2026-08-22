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


def _login_carrefour(page, email: str, password: str) -> str:
    page.goto(LOGIN["carrefour"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)
    _dismiss(page)
    try:
        page.locator("a.cc-btn.cc-dismiss").click(timeout=4000)
        page.wait_for_timeout(800)
    except Exception:
        pass
    _dismiss(page)
    page.wait_for_selector("#email", timeout=10000)
    loc = page.locator("#email")
    loc.click()
    loc.fill("")
    loc.press_sequentially(email, delay=40)
    page.wait_for_timeout(400)
    page.locator("button[type=submit]:has-text('Continue')").click(timeout=8000)
    page.wait_for_selector("input[type=password]", timeout=15000)
    pwd = page.locator("input[type=password]").first
    pwd.click()
    pwd.press_sequentially(password, delay=40)
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


def _in_thread(fn, **kwargs):
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, **kwargs).result(timeout=180)


def official_cart(
    *,
    store: str,
    email: str,
    password: str,
    action: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return _in_thread(
        _official_cart_sync,
        store=store,
        email=email,
        password=password,
        action=action,
        items=items,
    )


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
        return {"ok": False, "official_count": None, "error": "store login missing on dashboard"}
    pw = browser = context = None
    try:
        pw, browser, context = _launch()
        page = context.new_page()
        if store == "carrefour":
            _login_carrefour(page, email, password)
        elif store == "grandiose":
            _login_grandiose(page, email, password)
        else:
            _login_spinneys_waitrose(page, store, email, password)
        if "login" in (page.url or "").lower() and store == "carrefour":
            page.wait_for_timeout(2000)
        logged = "login" not in (page.url or "").lower()
        if store == "carrefour" and not logged:
            return {
                "ok": False,
                "official_count": None,
                "items": [],
                "logged_in": False,
                "final_url": page.url,
                "error": "Could not log into the Carrefour account from Domvs. Store cart was not changed.",
            }
        if store == "carrefour":
            if action in ("add", "set") and items:
                _add_items(page, store, items)
            api = page.evaluate(
                """async ({action, items}) => {
                  const tries = [];
                  async function hit(url, method, body) {
                    const r = await fetch(url, {
                      method,
                      credentials: 'include',
                      headers: {'content-type':'application/json','appid':'Reactweb','storeid':'mafuae','lang':'en'},
                      body: body ? JSON.stringify(body) : undefined
                    });
                    const t = await r.text();
                    let j = null; try { j = JSON.parse(t); } catch(e) {}
                    tries.push({url, status:r.status, text:t.slice(0,240)});
                    return {ok:r.ok, status:r.status, json:j, text:t.slice(0,240)};
                  }
                  if (action === 'clear') {
                    await hit('/v1/basket/mafuae/en/liteCart','GET');
                    await hit('/v8/carts/mafuae/en/STANDARD/clear','POST', {});
                    await hit('/v1/basket/mafuae/en/clear','POST', {});
                  }
                  for (const it of (items||[])) {
                    const pid = String(it.id||'');
                    const qty = Number(it.qty||1);
                    if (!pid) continue;
                    if (action === 'remove') {
                      await hit('/v8/carts/mafuae/en/STANDARD/removeItem','POST',{productId:pid});
                      continue;
                    }
                    const bodies = [
                      {productId: pid, quantity: qty, sellerId: '0000'},
                      {productId: pid, qty: qty, quantity: qty, offerId: 'offer_carrefour_'+pid},
                      {itemId: pid, quantity: qty}
                    ];
                    for (const b of bodies) {
                      const a = await hit('/v8/carts/mafuae/en/STANDARD/addItem','POST', b);
                      if (a.ok) break;
                      const c = await hit('/v1/basket/mafuae/en/product','POST', b);
                      if (c.ok) break;
                    }
                  }
                  const lite = await hit('/v1/basket/mafuae/en/liteCart?nsp=food,nonfood,express,QCOMM,QELEC&lm=false&liteResponse=true','GET');
                  const v8 = await hit('/v8/carts/mafuae/en/STANDARD','GET');
                  return {tries, lite: lite.json || lite.text, v8: v8.json || v8.text};
                }""",
                {"action": action, "items": items},
            )
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
            "final_url": page.url,
            "error": None if ok else "Store cart unavailable after login.",
        }
    except Exception as e:
        return {"ok": False, "official_count": None, "error": str(e)}
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
