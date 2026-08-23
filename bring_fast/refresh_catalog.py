"""Refresh catalog prices for bought products. Manual or fortnightly auto."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BRINGFAST_DATA", os.environ.get("BRINGFAST_DATA") or os.path.expanduser("~/.bring-fast"))

from bring_fast import compare, db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default="auto")
    p.add_argument("--max-age-days", type=int, default=12)
    p.add_argument("--sleep", type=float, default=0.25)
    args = p.parse_args()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = db.connect()
    users = [r["id"] for r in con.execute("SELECT id FROM users").fetchall()]
    con.close()
    done = skip = fail = 0
    for uid in users:
        products = compare.product_keys_for_user(uid)
        for prod in products:
            barcodes = [prod.get("barcode") or ""]
            names = [prod.get("official_name") or "", prod.get("receipt_name") or ""]
            for store in db.RETAILERS:
                if not compare.stale_before(uid, prod["product_key"], store["id"], cutoff):
                    skip += 1
                    continue
                q = compare.refresh_store(
                    uid,
                    prod["product_key"],
                    store["id"],
                    barcodes,
                    names,
                    source=args.source,
                )
                if q.get("ok"):
                    done += 1
                    print("OK", store["id"], prod["receipt_name"], q["price"], flush=True)
                else:
                    fail += 1
                    print("MISS", store["id"], prod["receipt_name"], q.get("error"), flush=True)
                time.sleep(args.sleep)
    print("done", done, "miss", fail, "skip", skip)


if __name__ == "__main__":
    main()
