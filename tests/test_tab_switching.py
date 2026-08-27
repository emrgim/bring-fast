"""Dock taps are bare /dashboard /purchases /stores.

Range, grain, the custom window and department are shared chrome on Home and Buys.
Sort and store stay on Buys only.
"""

import re

_CHIP = re.compile(r'<a class="([^"]*)"[^>]*>([^<]+)</a>')
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
    dept = _CHIP.findall(_section(head, "Department"))
    rng = _CHIP.findall(_section(head, "Range"))
    return {
        "dept_on": [label for cls, label in dept if cls == "on"],
        "range_on": [label for cls, label in rng if cls == "on"],
        "dept_chips": [label for _cls, label in dept],
        "range_chips": [label for _cls, label in rng],
        "dates": dict(_DATE.findall(head)),
        "custom_on": bool(re.search(r'<form method="get"[^>]*class="on"', head)),
        "grain_on": [label for cls, label in _CHIP.findall(grain_html) if cls == "on"],
    }


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


def test_range_chosen_on_home_is_on_buys(bf, client):
    _signin(bf, client, "sharehome@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    dest, buys = _tap(client, "/purchases")
    assert "range=1y" in dest
    assert "grain=yearly" in dest
    assert 'class="on" href="/purchases?sort=spend&dir=desc&range=1y&grain=yearly' in buys.text or ">1y<" in buys.text
    assert ">Yearly<" in buys.text


def test_range_chosen_on_buys_is_on_home(bf, client):
    _signin(bf, client, "sharebuys@example.com")
    client.get("/purchases", params={"sort": "frequency", "range": "3m", "grain": "weekly"})
    dest, home = _tap(client, "/dashboard")
    assert "range=3m" in dest
    assert "grain=weekly" in dest
    assert 'class="on" href="/dashboard?range=3m&grain=weekly"' in home.text
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest
    assert "range=3m" in dest


def test_sort_survives_a_range_change_on_the_other_tab(bf, client):
    _signin(bf, client, "sortkeep@example.com")
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks", "range": "1m", "grain": "monthly"})
    # Home range chips keep the selected department, same as Buys.
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest
    assert "dept=Drinks" in dest
    assert "range=1y" in dest
    assert "grain=yearly" in dest
    dest, home = _tap(client, "/dashboard")
    assert "range=1y" in dest
    assert "dept=Drinks" in dest
    assert "sort=" not in dest


def test_full_cycle_home_buys_stores_home_buys(bf, client):
    _signin(bf, client, "cycle@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks", "range": "3m", "grain": "weekly"})
    client.get("/stores")

    dest, home = _tap(client, "/dashboard")
    assert "range=3m" in dest and "grain=weekly" in dest
    assert "dept=Drinks" in dest
    assert 'class="on" href="/dashboard?range=3m&grain=weekly&dept=Drinks"' in home.text

    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest
    assert "dept=Drinks" in dest
    assert "range=3m" in dest
    assert "grain=weekly" in dest
    assert ">Likely<" in buys.text
    assert ">Drinks<" in buys.text

    stores = client.get("/stores")
    assert stores.status_code == 200
    assert 'aria-current="page"' in stores.text

    dest, home = _tap(client, "/dashboard")
    assert "range=3m" in dest and "grain=weekly" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest and "dept=Drinks" in dest and "range=3m" in dest


def test_filters_survive_many_switches(bf, client):
    _signin(bf, client, "many@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc", "range": "1w", "grain": "daily"})

    for _ in range(4):
        _tap(client, "/stores")
        dest, _home = _tap(client, "/dashboard")
        assert "range=1w" in dest and "grain=daily" in dest
        dest, _buys = _tap(client, "/purchases")
        assert "sort=name" in dest and "dir=asc" in dest
        assert "range=1w" in dest

    client.get("/dashboard", params={"range": "3m", "grain": "monthly"})
    dest, _buys = _tap(client, "/purchases")
    assert "sort=name" in dest
    assert "range=3m" in dest
    client.get("/purchases", params={"sort": "spend", "dir": "desc", "range": "all", "grain": "daily"})
    dest, _home = _tap(client, "/dashboard")
    assert "range=all" in dest and "grain=daily" in dest
    dest, _buys = _tap(client, "/purchases")
    assert "sort=spend" in dest and "dir=desc" in dest
    client.get("/stores")
    dest, _home = _tap(client, "/dashboard")
    assert "range=all" in dest
    dest, _buys = _tap(client, "/purchases")
    assert "sort=spend" in dest


def test_custom_range_survives_other_tabs(bf, client):
    _signin(bf, client, "custom@example.com")
    client.get(
        "/dashboard",
        params={"range": "custom", "start": "2026-01-01", "end": "2026-03-31", "grain": "monthly"},
    )
    client.get("/purchases", params={"sort": "times"})
    client.get("/stores")
    dest, home = _tap(client, "/dashboard")
    assert "range=custom" in dest
    assert "start=2026-01-01" in dest
    assert "end=2026-03-31" in dest
    assert 'action="/dashboard"' in home.text
    assert 'class="on filter-dates"' in home.text
    dest, _buys = _tap(client, "/purchases")
    assert "sort=times" in dest
    assert "range=custom" in dest
    assert "start=2026-01-01" in dest


def test_day_focus_is_shared_across_home_and_buys(bf, client):
    _signin(bf, client, "daytap@example.com")
    client.get("/dashboard", params={"range": "1m", "grain": "daily", "day": "2026-08-10"})
    dest, buys = _tap(client, "/purchases")
    assert "day=2026-08-10" in dest
    assert "grain=daily" in dest
    dest, home = _tap(client, "/dashboard")
    assert "day=2026-08-10" in dest
    client.get("/purchases", params={"sort": "frequency"})
    dest, home = _tap(client, "/dashboard")
    assert "day=2026-08-10" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest
    assert "day=2026-08-10" in dest


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
    assert "range=all" in dest and "grain=yearly" in dest
    dest, _buys = _tap(client, "/purchases")
    assert "sort=qty" in dest and "dept=Edible" in dest
    assert "range=all" in dest


def test_root_follows_whichever_tab_was_last(bf, client):
    _signin(bf, client, "root@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "monthly"})
    assert "/dashboard" in client.get("/", follow_redirects=False).headers["location"]
    client.get("/purchases", params={"sort": "times"})
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert "/purchases" in loc
    assert "range=1y" in loc
    client.get("/stores")
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/stores")
    _tap(client, "/dashboard")
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert loc.startswith("/dashboard")
    assert "range=1y" in loc


def test_first_visit_to_home_picks_up_the_buys_window(bf, client):
    _signin(bf, client, "first@example.com")
    client.get("/purchases", params={"sort": "frequency", "range": "1y"})
    dest, home = _tap(client, "/dashboard")
    assert "range=1y" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest and "range=1y" in dest
    assert home.status_code == 200


def test_interleaved_filter_changes_keep_the_latest(bf, client):
    _signin(bf, client, "latest@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/stores")
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks"})
    client.get("/stores/carrefour")

    dest, _home = _tap(client, "/dashboard")
    assert "range=1y" in dest and "grain=yearly" in dest
    assert "dept=Drinks" in dest
    assert "range=1w" not in dest
    dest, _buys = _tap(client, "/purchases")
    assert "sort=likely" in dest and "dept=Drinks" in dest
    assert "range=1y" in dest
    assert "sort=name" not in dest


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
        params={"sort": "frequency", "dir": "desc", "range": "2y", "grain": "monthly", "dept": "Edible", "store": "mmi,carrefour"},
    )
    dest, home = _tap(client, "/dashboard")
    assert "range=2y" in dest and "grain=monthly" in dest
    assert "dept=Edible" in dest
    assert 'class="on" href="/dashboard?range=2y&grain=monthly&dept=Edible"' in home.text
    client.get("/stores")
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest
    assert "range=2y" in dest
    assert "grain=monthly" in dest
    assert "dept=Edible" in dest
    assert "carrefour" in dest
    assert "mmi" in dest
    assert ">Edible<" in buys.text
    assert "Store ·" in buys.text
    assert home.status_code == 200


def test_department_chosen_on_home_is_on_buys(bf, client):
    _signin(bf, client, "homedept@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    dest, buys = _tap(client, "/purchases")
    assert "dept=Drinks" in dest
    assert "range=1y" in dest
    assert ">Drinks<" in buys.text
    dest, home = _tap(client, "/dashboard")
    assert "dept=Drinks" in dest
    assert 'class="on" href="/dashboard?range=1y&grain=yearly&dept=Drinks"' in home.text
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    dest, buys = _tap(client, "/purchases")
    assert "dept=" not in dest
    dest, home = _tap(client, "/dashboard")
    assert "dept=" not in dest
    assert 'class="on" href="/dashboard?range=1y&grain=yearly"' in home.text


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
    first = _filter_view(home.text)
    assert first["dept_chips"] == ["All", "Edible", "Drinks"]
    assert first["range_chips"] == ["1w", "2w", "1m", "3m", "1y", "2y", "3y", "All"]
    assert first["dept_on"] == ["All"]
    assert first["range_on"] == ["1m"]
    assert first["grain_on"] == ["Monthly"]
    snapshots = [("home0", dest, first)]

    for i in range(12):
        dest_b, buys = _tap(client, "/purchases")
        buys_view = _filter_view(buys.text)
        dest_s, stores = _tap(client, "/stores")
        assert stores.status_code == 200
        dest_h, home = _tap(client, "/dashboard")
        home_view = _filter_view(home.text)
        snapshots.append((f"buys{i+1}", dest_b, buys_view))
        snapshots.append((f"home{i+1}", dest_h, home_view))
        assert home_view == first, (
            f"Home filters changed after switch {i + 1} ({dest_h}): {first} → {home_view}"
        )
        shared_keys = ("dept_on", "range_on", "dept_chips", "range_chips", "grain_on", "custom_on")
        for key in shared_keys:
            assert buys_view[key] == first[key], (
                f"Buys shared filter {key} drifted on switch {i + 1} ({dest_b}): "
                f"{first[key]} → {buys_view[key]}"
            )


def test_chosen_filters_do_not_drift_across_twelve_switches(bf, client):
    _seed_receipts(bf, "chosen12@example.com")
    client.post("/login", data={"email": "chosen12@example.com", "password": "secret1", "intent": "signin"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly", "dept": "Drinks"})
    client.get("/purchases", params={"sort": "likely", "dir": "desc"})

    dest, home = _tap(client, "/dashboard")
    first = _filter_view(home.text)
    assert first["dept_on"] == ["Drinks"]
    assert first["range_on"] == ["1y"]
    assert first["grain_on"] == ["Yearly"]

    for i in range(12):
        dest_b, buys = _tap(client, "/purchases")
        _tap(client, "/stores")
        dest_h, home = _tap(client, "/dashboard")
        assert _filter_view(home.text) == first, (
            f"Home drifted on switch {i + 1} ({dest_h}): {_filter_view(home.text)}"
        )
        buys_view = _filter_view(buys.text)
        assert buys_view["dept_on"] == ["Drinks"]
        assert buys_view["range_on"] == ["1y"]
        assert buys_view["grain_on"] == ["Yearly"]
        assert "sort=likely" in dest_b


def test_home_and_buys_show_the_same_filter_chips(bf, client):
    """Switching tabs must not grow or shrink the shared filter bar."""
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
    assert buys_view["range_on"] == first["range_on"]
    assert buys_view["grain_on"] == first["grain_on"]
    assert buys_view["range_on"] != ["All"] or first["range_on"] == ["All"]
