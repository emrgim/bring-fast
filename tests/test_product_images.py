from bring_fast import product_images


def test_compress_writes_small_webp(tmp_path):
    from PIL import Image

    src = tmp_path / "big.png"
    Image.new("RGB", (800, 800), (200, 0, 0)).save(src)
    data = product_images.compress_bytes(src.read_bytes())
    assert data[:4] == b"RIFF"
    assert len(data) < src.stat().st_size


def test_attach_reuses_existing_file(bf):
    dest = product_images.local_path("name:apple-pie")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"RIFF....WEBP")
    url = product_images.attach_for_receipt("mcdonalds", "name:apple-pie", "Apple Pie")
    assert url == "/product-images/name_apple-pie.webp"
    meta = bf.purchases.get_product_meta("name:apple-pie")
    assert meta and meta["image_url"] == url


def test_upsert_does_not_fetch_when_disabled(bf):
    user = bf.db.create_user("img@example.com", "secret1")
    iid = bf.purchases.upsert_invoice(
        user["id"],
        {
            "retailer": "mcdonalds",
            "invoice_no": "M1",
            "invoice_date": "2026-01-01",
            "items": [{"name": "Apple Pie", "qty": 1, "line_total": 5, "unit_price": 5}],
        },
    )
    assert iid
    con = bf.db.connect()
    row = con.execute("SELECT image_url FROM invoice_items WHERE invoice_id=?", (iid,)).fetchone()
    con.close()
    assert (row["image_url"] or "") == ""
