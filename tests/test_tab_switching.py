"""Dock taps are bare /dashboard /purchases /stores.

Range, grain, the custom window and department are shared chrome on Home and Buys.
Sort and store stay on Buys only.
"""


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
    assert 'action="/dashboard" class="on"' in home.text
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
