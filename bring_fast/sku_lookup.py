"""Look up a GTIN/EAN/SKU on Open * Facts (universal barcode DBs)."""

from __future__ import annotations

from typing import Any

import requests

UA = "BringFast/1.4.1 (e@mrg.im) sku-lookup"
HOSTS = (
    "https://world.openfoodfacts.org",
    "https://world.openbeautyfacts.org",
    "https://world.openproductsfacts.org",
)


def _human_cat(tags: list[str], raw: str) -> str:
    en = [t.split(":", 1)[-1] for t in tags if str(t).startswith("en:")]
    if en:
        return en[-1].replace("-", " ")
    if raw:
        return raw.split(",")[-1].strip()
    return ""


def lookup_gtin(code: str) -> dict[str, Any] | None:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    variants = [digits]
    if 8 <= len(digits) < 13:
        variants.append(digits.zfill(13))
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})
    for host in HOSTS:
        for gtin in variants:
            try:
                r = sess.get(
                    f"{host}/api/v2/product/{gtin}.json",
                    params={
                        "fields": "product_name,product_name_en,brands,categories,categories_tags,image_front_url,image_url"
                    },
                    timeout=18,
                )
            except requests.RequestException:
                continue
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            if data.get("status") != 1:
                continue
            p = data.get("product") or {}
            name = (p.get("product_name_en") or p.get("product_name") or "").strip()
            cat = _human_cat(p.get("categories_tags") or [], p.get("categories") or "")
            img = p.get("image_front_url") or p.get("image_url") or ""
            return {
                "sku": gtin,
                "name": name,
                "category": cat,
                "image_url": img,
                "brands": (p.get("brands") or "").strip(),
                "source": host.split("//", 1)[-1].split(".")[1],
            }
    return None
