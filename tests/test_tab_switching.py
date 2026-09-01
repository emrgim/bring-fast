"""Dock taps are bare /dashboard /purchases /stores.

Each tab restores the filters left on that tab. Home and Buys do not share a
window: switching must not copy range, grain, department, day or dates.
Sort and store stay on Buys only.
"""

import re
from urllib.parse import parse_qsl, urlsplit

_CHIP = re.compile(r'<a class="([^"]*)"[^>]*>([^<]+)</a>')
_ALL_BTN = re.compile(
    r'<button[^>]*id="category-toggle"[^>]*class="([^"]*)"[^>]*>([^<]+)</button>'
    r'|<button[^>]*class="([^"]*)"[^>]*id="category-toggle"[^>]*>([^<]+)</button>'
)
_DATE = re.compile(r'<input type="date" name="(start|end)" value="([^"]*)"')


def _section(html, label):
    found = re.search(rf'aria-label="{label}"(.*?)(?:</div>|</form>)', html, re.S)
    return found.group(1) if found else ""


def _filter_view(html):
    """What the filter bar actually shows: chips, which are on, dates, grain."""
    if '<header class="app-head">' not in html:
        return {}
    head = html.split('<header class="app-head">', 1)[1].split("</header>", 1)[0]
    grain = re.search(r'<div class="grain">(.*?)</div>', html, re.S)
    grain_html = grain.group(1) if grain else ""
    dept_html = _section(head, "Department")
    dept = _CHIP.findall(dept_html)
    all_btn = _ALL_BTN.search(dept_html)
    dept_chips = []
    if all_btn:
        label = all_btn.group(2) or all_btn.group(4) or ""
        dept_chips.append(label.split("·")[0].strip())
    dept_chips.extend([label for _cls, label in dept])
    dept_on = []
    if all_btn:
        cls = all_btn.group(1) or all_btn.group(3) or ""
        if " on" in (" " + cls + " "):
            dept_on.append("All")
    dept_on.extend([label for cls, label in dept if cls == "on"])
    rng = _CHIP.findall(_section(head, "Range"))
    return {
        "dept_on": dept_on,
        "range_on": [label for cls, label in rng if cls == "on"],
        "dept_chips": dept_chips,
        "range_chips": [label for _cls, label in rng],
        "dates": dict(_DATE.findall(head)),
        "custom_on": bool(re.search(r'<form method="get"[^>]*class="on"', head)),
        "grain_on": [label for cls, label in _CHIP.findall(grain_html) if cls == "on"],
    }


def _q(dest):
    return dict(parse_qsl(urlsplit(dest).query, keep_blank_values=True))


def _signin(bf, client, email):
    bf.db.create_user(email, "secret1")
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def _tap(client, path):
    """A dock tap: open the bare tab URL and follow the restore redirect."""
    r = client.get(path, follow_redirects=False)
    if r.status_code == 303:
        dest = r.headers["location"]
        return dest, client.get(dest)
    return path, r


def test_range_chosen_on_home_stays_off_buys(bf, client):
    _signin(bf, client, "sharehome@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    dest, buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("range") != "1y"
    assert q.get("grain") != "yearly"
    dest, home = _tap(client, "/dashboard")
    assert _q(dest).get("range") == "1y"
    assert _q(dest).get("grain") == "yearly"
    assert ">Yearly<" in home.text
    assert ">1y<" in home.text or 'range=1y' in home.text
    assert buys.status_code == 200


def test_range_chosen_on_buys_stays_off_home(bf, client):
    _signin(bf, client, "sharebuys@example.com")
    client.get("/purchases", params={"sort": "frequency", "range": "3m", "grain": "weekly"})
    dest, home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") != "3m"
    assert q.get("grain") != "weekly"
    dest, buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "frequency"
    assert q.get("range") == "3m"
    assert q.get("grain") == "weekly"
    assert home.status_code == 200
    assert buys.status_code == 200


def test_sort_survives_a_range_change_on_the_other_tab(bf, client):
    _signin(bf, client, "sortkeep@example.com")
    client.get(
        "/purchases",
        params={"sort": "likely", "dir": "desc", "dept": "Drinks", "range": "1m", "grain": "monthly"},
    )
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "likely"
    assert q.get("dept") == "Drinks"
    assert q.get("range") == "1m"
    assert q.get("grain") == "monthly"
    dest, _home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") == "1y"
    assert q.get("dept") == "Drinks"
    assert "sort" not in q


def test_full_cycle_home_buys_stores_home_buys(bf, client):
    _signin(bf, client, "cycle@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get(
        "/purchases",
        params={"sort": "likely", "dir": "desc", "dept": "Drinks", "range": "3m", "grain": "weekly"},
    )
    client.get("/stores")

    dest, home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") == "1y" and q.get("grain") == "yearly"
    assert q.get("dept") != "Drinks"
    assert 'class="on" href="/dashboard?range=1y&grain=yearly"' in home.text

    dest, buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "likely"
    assert q.get("dept") == "Drinks"
    assert q.get("range") == "3m"
    assert q.get("grain") == "weekly"
    assert ">Likely<" in buys.text
    assert ">Drinks<" in buys.text

    stores = client.get("/stores")
    assert stores.status_code == 200
    assert 'aria-current="page"' in stores.text

    dest, _home = _tap(client, "/dashboard")
    assert _q(dest).get("range") == "1y" and _q(dest).get("grain") == "yearly"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "likely" and q.get("dept") == "Drinks" and q.get("range") == "3m"


def test_filters_survive_many_switches(bf, client):
    _signin(bf, client, "many@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc", "range": "1w", "grain": "daily"})

    for _ in range(4):
        _tap(client, "/stores")
        dest, _home = _tap(client, "/dashboard")
        assert _q(dest).get("range") == "1w" and _q(dest).get("grain") == "daily"
        dest, _buys = _tap(client, "/purchases")
        q = _q(dest)
        assert q.get("sort") == "name" and q.get("dir") == "asc"
        assert q.get("range") == "1w"

    client.get("/dashboard", params={"range": "3m", "grain": "monthly"})
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "name"
    assert q.get("range") == "1w"
    client.get("/purchases", params={"sort": "spend", "dir": "desc", "range": "all", "grain": "daily"})
    dest, _home = _tap(client, "/dashboard")
    assert _q(dest).get("range") == "3m" and _q(dest).get("grain") == "monthly"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "spend" and q.get("dir") == "desc"
    assert q.get("range") == "all"
    client.get("/stores")
    dest, _home = _tap(client, "/dashboard")
    assert _q(dest).get("range") == "3m"
    dest, _buys = _tap(client, "/purchases")
    assert _q(dest).get("sort") == "spend"


def test_custom_range_survives_other_tabs(bf, client):
    _signin(bf, client, "custom@example.com")
    client.get(
        "/dashboard",
        params={"range": "custom", "start": "2026-01-01", "end": "2026-03-31", "grain": "monthly"},
    )
    client.get("/purchases", params={"sort": "times"})
    client.get("/stores")
    dest, home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") == "custom"
    assert q.get("start") == "2026-01-01"
    assert q.get("end") == "2026-03-31"
    assert 'action="/dashboard"' in home.text
    assert 'class="on filter-dates"' in home.text
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "times"
    assert q.get("range") != "custom"
    assert q.get("start") != "2026-01-01"


def test_day_focus_stays_on_the_tab_that_set_it(bf, client):
    _signin(bf, client, "daytap@example.com")
    client.get("/dashboard", params={"range": "1m", "grain": "daily", "day": "2026-08-10"})
    dest, _buys = _tap(client, "/purchases")
    assert _q(dest).get("day") != "2026-08-10"
    dest, _home = _tap(client, "/dashboard")
    assert _q(dest).get("day") == "2026-08-10"
    client.get("/purchases", params={"sort": "frequency"})
    dest, _home = _tap(client, "/dashboard")
    assert _q(dest).get("day") == "2026-08-10"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "frequency"
    assert q.get("day") != "2026-08-10"


def test_store_page_then_dock_tabs(bf, client):
    _signin(bf, client, "storehop@example.com")
    client.get("/dashboard", params={"range": "all", "grain": "yearly"})
    client.get("/purchases", params={"sort": "qty", "dir": "desc", "dept": "Edible"})
    page = client.get("/stores/grandiose")
    assert page.status_code == 200
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"].startswith("/stores/grandiose")

    dest, _home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") == "all" and q.get("grain") == "yearly"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "qty" and q.get("dept") == "Edible"
    assert q.get("grain") != "yearly"


def test_root_follows_whichever_tab_was_last(bf, client):
    _signin(bf, client, "root@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "monthly"})
    assert "/dashboard" in client.get("/", follow_redirects=False).headers["location"]
    client.get("/purchases", params={"sort": "times"})
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert "/purchases" in loc
    assert _q(loc).get("range") != "1y"
    assert _q(loc).get("sort") == "times"
    client.get("/stores")
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/stores")
    _tap(client, "/dashboard")
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert loc.startswith("/dashboard")
    assert _q(loc).get("range") == "1y"


def test_first_visit_to_home_does_not_pick_up_the_buys_window(bf, client):
    _signin(bf, client, "first@example.com")
    client.get("/purchases", params={"sort": "frequency", "range": "1y"})
    dest, home = _tap(client, "/dashboard")
    assert _q(dest).get("range") != "1y"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "frequency" and q.get("range") == "1y"
    assert home.status_code == 200


def test_interleaved_filter_changes_keep_each_tab(bf, client):
    _signin(bf, client, "latest@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/stores")
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks"})
    client.get("/stores/carrefour")

    dest, _home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") == "1y" and q.get("grain") == "yearly"
    assert q.get("dept") != "Drinks"
    assert q.get("range") != "1w"
    dest, _buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "likely" and q.get("dept") == "Drinks"
    assert q.get("sort") != "name"
    assert q.get("range") != "1y"


def test_buys_store_and_dept_survive_home_and_stores(bf, client):
    user = bf.db.create_user("chips@example.com", "secret1")
    bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "carrefour",
            "invoice_no": "c1",
            "invoice_date": "2026-08-10",
            "items": [{"name": "Milk", "qty": 1, "unit_price": 10, "line_total": 10, "barcode": "1"}],
        },
    )
    client.post("/login", data={"email": "chips@example.com", "password": "secret1", "intent": "signin"})
    client.get(
        "/purchases",
        params={
            "sort": "frequency",
            "dir": "desc",
            "range": "2y",
            "grain": "monthly",
            "dept": "Edible",
            "store": "mmi,carrefour",
        },
    )
    dest, home = _tap(client, "/dashboard")
    q = _q(dest)
    assert q.get("range") != "2y"
    assert q.get("dept") != "Edible"
    client.get("/stores")
    dest, buys = _tap(client, "/purchases")
    q = _q(dest)
    assert q.get("sort") == "frequency"
    assert q.get("range") == "2y"
    assert q.get("grain") == "monthly"
    assert q.get("dept") == "Edible"
    assert "carrefour" in dest
    assert "mmi" in dest
    assert ">Edible<" in buys.text
    assert "Store ·" in buys.text
    assert home.status_code == 200


def test_department_chosen_on_home_stays_off_buys(bf, client):
    _signin(bf, client, "homedept@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    dest, buys = _tap(client, "/purchases")
    assert _q(dest).get("dept") != "Drinks"
    dest, home = _tap(client, "/dashboard")
    assert _q(dest).get("dept") == "Drinks"
    assert 'class="on" href="/dashboard?range=1y&grain=yearly&dept=Drinks"' in home.text
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    dest, _buys = _tap(client, "/purchases")
    dest, home = _tap(client, "/dashboard")
    assert "dept" not in _q(dest)
    assert 'class="on" href="/dashboard?range=1y&grain=yearly"' in home.text
    assert buys.status_code == 200


def test_all_on_home_does_not_clear_buys_department(bf, client):
    _signin(bf, client, "allhome@example.com")
    client.get("/purchases", params={"sort": "likely", "dept": "Drinks", "range": "all", "grain": "daily"})
    client.get("/dashboard", params={"range": "1m", "grain": "monthly", "dept": "Edible"})
    client.get("/dashboard", params={"range": "1m", "grain": "monthly"})
    dest, _home = _tap(client, "/dashboard")
    assert "dept" not in _q(dest)
    dest, _buys = _tap(client, "/purchases")
    assert _q(dest).get("dept") == "Drinks"


def _seed_receipts(bf, email):
    user = bf.db.create_user(email, "secret1")
    for i, day in enumerate(("2026-08-01", "2026-08-10", "2026-05-01", "2025-08-20")):
        bf.purchases.upsert_invoice(
            user["id"],
            {
                "retailer": "carrefour" if i % 2 == 0 else "grandiose",
                "invoice_no": f"{email}-{i}",
                "invoice_date": day,
                "items": [
                    {
                        "name": "Milk" if i % 2 == 0 else "Heineken Cans 50 cl",
                        "qty": 1,
                        "unit_price": 10 + i,
                        "line_total": 10 + i,
                        "barcode": str(100 + i),
                    }
                ],
            },
        )
    return user


def test_home_filter_bar_does_not_drift_across_twelve_tab_switches(bf, client):
    """A dock tap is not a filter. Home → Buys → Stores → Home, twelve times."""
    _seed_receipts(bf, "twelve@example.com")
    client.post("/login", data={"email": "twelve@example.com", "password": "secret1", "intent": "signin"})

    dest, home = _tap(client, "/dashboard")
    home_first = _filter_view(home.text)
    assert home_first["dept_chips"] == ["All", "Edible", "Drinks"]
    assert home_first["range_chips"] == ["1w", "2w", "1m", "3m", "1y", "2y", "3y", "All"]
    assert home_first["dept_on"] == ["All"]
    assert home_first["range_on"] == ["1m"]
    assert home_first["grain_on"] == ["Monthly"]

    dest_b, buys = _tap(client, "/purchases")
    buys_first = _filter_view(buys.text)
    assert buys_first["range_on"] == ["All"]
    assert buys_first["grain_on"] == ["Daily"]
    assert buys_first["range_on"] != home_first["range_on"]

    for i in range(12):
        dest_b, buys = _tap(client, "/purchases")
        buys_view = _filter_view(buys.text)
        dest_s, stores = _tap(client, "/stores")
        assert stores.status_code == 200
        dest_h, home = _tap(client, "/dashboard")
        home_view = _filter_view(home.text)
        assert home_view == home_first, (
            f"Home filters changed after switch {i + 1} ({dest_h}): {home_first} → {home_view}"
        )
        assert buys_view == buys_first, (
            f"Buys filters changed after switch {i + 1} ({dest_b}): {buys_first} → {buys_view}"
        )


def test_chosen_filters_do_not_drift_across_twelve_switches(bf, client):
    _seed_receipts(bf, "chosen12@example.com")
    client.post("/login", data={"email": "chosen12@example.com", "password": "secret1", "intent": "signin"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "range": "all", "grain": "daily"})

    dest, home = _tap(client, "/dashboard")
    home_first = _filter_view(home.text)
    assert home_first["dept_on"] == ["Drinks"]
    assert home_first["range_on"] == ["1y"]
    assert home_first["grain_on"] == ["Yearly"]
    dest_b, buys = _tap(client, "/purchases")
    buys_first = _filter_view(buys.text)
    assert buys_first["dept_on"] == ["All"]
    assert buys_first["range_on"] == ["All"]
    assert buys_first["grain_on"] == ["Daily"]
    assert "sort=likely" in dest_b

    for i in range(12):
        dest_b, buys = _tap(client, "/purchases")
        _tap(client, "/stores")
        dest_h, home = _tap(client, "/dashboard")
        assert _filter_view(home.text) == home_first, (
            f"Home drifted on switch {i + 1} ({dest_h}): {_filter_view(home.text)}"
        )
        assert _filter_view(buys.text) == buys_first, (
            f"Buys drifted on switch {i + 1} ({dest_b}): {_filter_view(buys.text)}"
        )
        assert "sort=likely" in dest_b


def test_home_and_buys_show_the_same_filter_chips(bf, client):
    """Switching tabs must not grow or shrink the filter bar chrome."""
    _signin(bf, client, "samechips@example.com")
    _, home = _tap(client, "/dashboard")
    _, buys = _tap(client, "/purchases")
    _, home2 = _tap(client, "/dashboard")
    home_view = _filter_view(home.text)
    buys_view = _filter_view(buys.text)
    home2_view = _filter_view(home2.text)
    assert home_view["dept_chips"] == buys_view["dept_chips"] == ["All", "Edible", "Drinks"]
    assert home_view["range_chips"] == buys_view["range_chips"] == [
        "1w",
        "2w",
        "1m",
        "3m",
        "1y",
        "2y",
        "3y",
        "All",
    ]
    assert home_view["dept_chips"] == home2_view["dept_chips"]
    assert home_view["range_chips"] == home2_view["range_chips"]


def test_buys_first_visit_does_not_replace_home_window_on_the_way_back(bf, client):
    """Home's 1m/Monthly must survive a first dock tap on Buys, which defaults to all/daily."""
    _seed_receipts(bf, "defaults@example.com")
    client.post("/login", data={"email": "defaults@example.com", "password": "secret1", "intent": "signin"})
    _, home = _tap(client, "/dashboard")
    first = _filter_view(home.text)
    assert first["range_on"] == ["1m"]
    assert first["grain_on"] == ["Monthly"]

    _, buys = _tap(client, "/purchases")
    buys_view = _filter_view(buys.text)
    _, home2 = _tap(client, "/dashboard")
    back = _filter_view(home2.text)
    assert back == first, f"Coming back from Buys changed Home: {first} → {back}"
    assert buys_view["range_on"] == ["All"]
    assert buys_view["grain_on"] == ["Daily"]


def test_two_or_three_switches_do_not_apply_the_other_tabs_filters(bf, client):
    """The bug showed up on the second or third dock tap, not the first."""
    _seed_receipts(bf, "twothree@example.com")
    client.post("/login", data={"email": "twothree@example.com", "password": "secret1", "intent": "signin"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    client.get("/purchases", params={"sort": "times", "dir": "desc", "range": "3m", "grain": "weekly"})

    dest, home = _tap(client, "/dashboard")
    home_first = _filter_view(home.text)
    dest, buys = _tap(client, "/purchases")
    buys_first = _filter_view(buys.text)
    assert home_first["range_on"] == ["1y"]
    assert home_first["grain_on"] == ["Yearly"]
    assert home_first["dept_on"] == ["Drinks"]
    assert buys_first["range_on"] == ["3m"]
    assert buys_first["grain_on"] == ["Weekly"]
    assert buys_first["dept_on"] == ["All"]

    for i in range(3):
        _tap(client, "/stores")
        dest_h, home = _tap(client, "/dashboard")
        dest_b, buys = _tap(client, "/purchases")
        dest_h, home = _tap(client, "/dashboard")
        dest_b, buys = _tap(client, "/purchases")
        assert _filter_view(home.text) == home_first, f"Home drifted on round {i + 1} ({dest_h})"
        assert _filter_view(buys.text) == buys_first, f"Buys drifted on round {i + 1} ({dest_b})"
        assert _q(dest_h).get("range") == "1y"
        assert _q(dest_b).get("range") == "3m"
        assert _q(dest_b).get("sort") == "times"
