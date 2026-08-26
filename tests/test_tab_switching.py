"""Dock taps are bare /dashboard /purchases /stores. Filters must survive the hop."""


def _signin(bf, client, email):
    bf.db.create_user(email, "secret1")
    client.post("/login", data={"email": email, "password": "secret1", "intent": "signin"})


def _loc(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303, (path, r.status_code, r.headers.get("location"))
    return r.headers["location"]


def _tap(client, path):
    """A dock tap: open the bare tab URL and follow the restore redirect."""
    r = client.get(path, follow_redirects=False)
    if r.status_code == 303:
        dest = r.headers["location"]
        return dest, client.get(dest)
    return path, r


def test_full_cycle_home_buys_stores_home_buys(bf, client):
    _signin(bf, client, "cycle@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks", "range": "3m", "grain": "weekly"})
    client.get("/stores")

    dest, home = _tap(client, "/dashboard")
    assert "range=1y" in dest and "grain=yearly" in dest
    assert 'class="on" href="/dashboard?range=1y&grain=yearly"' in home.text
    assert ">Yearly<" in home.text

    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest
    assert "dept=Drinks" in dest
    assert "range=3m" in dest
    assert "grain=weekly" in dest
    assert 'class="on"' in buys.text
    assert ">Likely<" in buys.text
    assert ">Drinks<" in buys.text

    stores = client.get("/stores")
    assert stores.status_code == 200
    assert 'aria-current="page"' in stores.text

    dest, home = _tap(client, "/dashboard")
    assert "range=1y" in dest and "grain=yearly" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest and "dept=Drinks" in dest


def test_filters_stay_independent_after_many_switches(bf, client):
    _signin(bf, client, "many@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc", "range": "all", "grain": "daily"})

    for _ in range(4):
        dest, _page = _tap(client, "/stores")
        dest, home = _tap(client, "/dashboard")
        assert "range=1w" in dest and "grain=daily" in dest
        dest, buys = _tap(client, "/purchases")
        assert "sort=name" in dest and "dir=asc" in dest

    client.get("/dashboard", params={"range": "3m", "grain": "monthly"})
    dest, buys = _tap(client, "/purchases")
    assert "sort=name" in dest
    client.get("/purchases", params={"sort": "spend", "dir": "desc", "range": "all", "grain": "daily"})
    dest, home = _tap(client, "/dashboard")
    assert "range=3m" in dest and "grain=monthly" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=spend" in dest and "dir=desc" in dest
    client.get("/stores")
    dest, home = _tap(client, "/dashboard")
    assert "range=3m" in dest
    dest, buys = _tap(client, "/purchases")
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


def test_day_focus_survives_a_tab_switch(bf, client):
    _signin(bf, client, "daytap@example.com")
    client.get("/dashboard", params={"range": "1m", "grain": "daily", "day": "2026-08-10"})
    client.get("/purchases", params={"sort": "frequency"})
    dest, home = _tap(client, "/dashboard")
    assert "day=2026-08-10" in dest
    assert "grain=daily" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest
    assert "day=" not in dest


def test_store_page_then_dock_tabs(bf, client):
    _signin(bf, client, "storehop@example.com")
    client.get("/dashboard", params={"range": "all", "grain": "yearly"})
    client.get("/purchases", params={"sort": "qty", "dir": "desc", "dept": "Edible"})
    page = client.get("/stores/grandiose")
    assert page.status_code == 200
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"].startswith("/stores/grandiose")

    dest, home = _tap(client, "/dashboard")
    assert "range=all" in dest and "grain=yearly" in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=qty" in dest and "dept=Edible" in dest


def test_root_follows_whichever_tab_was_last(bf, client):
    _signin(bf, client, "root@example.com")
    client.get("/dashboard", params={"range": "1y", "grain": "monthly"})
    assert "/dashboard" in client.get("/", follow_redirects=False).headers["location"]
    client.get("/purchases", params={"sort": "times"})
    assert "/purchases" in client.get("/", follow_redirects=False).headers["location"]
    client.get("/stores")
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/stores")
    _tap(client, "/dashboard")
    loc = client.get("/", follow_redirects=False).headers["location"]
    assert loc.startswith("/dashboard")
    assert "range=1y" in loc


def test_first_visit_to_a_tab_does_not_steal_the_other(bf, client):
    _signin(bf, client, "first@example.com")
    client.get("/purchases", params={"sort": "frequency", "range": "1y"})
    bare = client.get("/dashboard", follow_redirects=False)
    assert bare.status_code == 200
    dest, buys = _tap(client, "/purchases")
    assert "sort=frequency" in dest and "range=1y" in dest


def test_interleaved_filter_changes_keep_the_latest_per_tab(bf, client):
    _signin(bf, client, "latest@example.com")
    client.get("/dashboard", params={"range": "1w", "grain": "daily"})
    client.get("/purchases", params={"sort": "name", "dir": "asc"})
    client.get("/dashboard", params={"range": "1y", "grain": "yearly"})
    client.get("/stores")
    client.get("/purchases", params={"sort": "likely", "dir": "desc", "dept": "Drinks"})
    client.get("/stores/carrefour")

    dest, home = _tap(client, "/dashboard")
    assert "range=1y" in dest and "grain=yearly" in dest
    assert "range=1w" not in dest
    dest, buys = _tap(client, "/purchases")
    assert "sort=likely" in dest and "dept=Drinks" in dest
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
    _tap(client, "/dashboard")
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
