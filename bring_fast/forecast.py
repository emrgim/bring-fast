"""Explainable buy-again forecast: EWMA + regularity, not a plain mean."""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

from . import db
from .depts import classify_dept, normalize_dept

DEFAULTS = {
    "min_buys": 4,
    "max_interval_days": 45,
    "max_cv": 0.55,
    "ewma_alpha": 0.4,
    "lapsed_factor": 2.0,
    "max_last_age_days": 90,
}

SKIP_NAME = (
    "plastic bag",
    "shopping bag",
    "plast shopping",
    "t shirt hndl bag",
    "delivery fee",
    "service fee",
)

SMALL_PACK = re.compile(
    r"(500\s*ml|50\s*cl|0\.5\s*l|330\s*ml|1\s*can|1\s*bottle|singol)",
    re.I,
)
MULTI_PACK = re.compile(r"(\d+\s*x|pack|case|cs\d|24\s*x|12\s*x)", re.I)
WATERISH = re.compile(r"(san\s*pel|acqua|water|sparkling|oasis|perrier|pellegrino)", re.I)


def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def successive_intervals(days: list[date]) -> list[int]:
    ordered = sorted(set(days))
    out = []
    for a, b in zip(ordered, ordered[1:]):
        gap = (b - a).days
        if gap > 0:
            out.append(gap)
    return out


def ewma(values: list[float], alpha: float = 0.4) -> float:
    if not values:
        return 0.0
    acc = float(values[0])
    for x in values[1:]:
        acc = alpha * float(x) + (1.0 - alpha) * acc
    return acc


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    mu = sum(values) / n
    var = sum((x - mu) ** 2 for x in values) / n
    return mu, math.sqrt(var)


def pack_reason(name: str) -> str | None:
    blob = name or ""
    if SMALL_PACK.search(blob) and WATERISH.search(blob) and not MULTI_PACK.search(blob):
        return "occasional_small_pack"
    return None


def load_exclusions(user_id: int) -> set[str]:
    con = db.connect()
    rows = con.execute("SELECT product_key FROM forecast_exclusions WHERE user_id=?", (user_id,)).fetchall()
    con.close()
    return {r["product_key"] for r in rows}


def set_exclusion(user_id: int, product_key: str, *, on: bool = True) -> None:
    con = db.connect()
    if on:
        con.execute(
            "INSERT OR REPLACE INTO forecast_exclusions(user_id, product_key, created_at) VALUES (?,?,?)",
            (user_id, product_key, date.today().isoformat()),
        )
    else:
        con.execute("DELETE FROM forecast_exclusions WHERE user_id=? AND product_key=?", (user_id, product_key))
    con.commit()
    con.close()


def load_votes(user_id: int) -> dict[str, str]:
    con = db.connect()
    rows = con.execute("SELECT product_key, vote FROM forecast_votes WHERE user_id=?", (user_id,)).fetchall()
    con.close()
    return {r["product_key"]: r["vote"] for r in rows if r["vote"] in ("up", "down")}


def set_vote(user_id: int, product_key: str, vote: str = "") -> str:
    vote = (vote or "").strip().lower()
    if vote not in ("up", "down"):
        vote = ""
    con = db.connect()
    if vote:
        con.execute(
            "INSERT OR REPLACE INTO forecast_votes(user_id, product_key, vote, updated_at) VALUES (?,?,?,?)",
            (user_id, product_key, vote, date.today().isoformat()),
        )
    else:
        con.execute("DELETE FROM forecast_votes WHERE user_id=? AND product_key=?", (user_id, product_key))
    con.commit()
    con.close()
    return vote


def apply_vote(row: dict[str, Any], vote: str = "") -> dict[str, Any]:
    """Shift a computed likely score after the user thumbs a product."""
    vote = (vote or "").strip().lower()
    if vote not in ("up", "down"):
        vote = ""
    row["vote"] = vote
    row["likely_vote"] = vote
    score = int(row.get("score") or 0)
    if vote == "up":
        row["score"] = min(100, max(score + 40, 55))
        if row.get("reason") != "noise":
            row["include"] = True
    elif vote == "down":
        row["score"] = int(round(score * 0.2))
        row["include"] = False
    return row


def buy_history(
    user_id: int,
    *,
    since: str | None = None,
    until: date | None = None,
    keys: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    con = db.connect()
    where = "WHERE i.user_id=?"
    args: list[Any] = [user_id]
    if since:
        where += " AND substr(i.invoice_date,1,10)>=?"
        args.append(since)
    if until:
        where += " AND substr(i.invoice_date,1,10)<=?"
        args.append(until.isoformat())
    if keys is not None:
        wanted = [k for k in keys if k]
        if not wanted:
            con.close()
            return {}
        # Stay under SQLite's bound-variable cap. A huge shelf falls back to
        # the unfiltered scan rather than failing the tab.
        if len(wanted) <= 800:
            where += " AND it.product_key IN (" + ",".join("?" * len(wanted)) + ")"
            args.extend(wanted)
    rows = con.execute(
        f"""
        SELECT it.product_key,
               MAX(it.name) AS receipt_name,
               MAX(NULLIF(pm.official_name,'')) AS official_name,
               MAX(it.barcode) AS barcode,
               i.invoice_date,
               SUM(it.qty) AS qty
        FROM invoice_items it
        JOIN invoices i ON i.id = it.invoice_id
        LEFT JOIN product_meta pm ON pm.product_key = it.product_key
        {where}
        GROUP BY it.product_key, i.id
        ORDER BY it.product_key, i.invoice_date
        """,
        args,
    ).fetchall()
    con.close()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["product_key"]
        rec = out.setdefault(
            key,
            {
                "key": key,
                "receipt_name": r["receipt_name"] or "",
                "official_name": r["official_name"] or "",
                "barcode": r["barcode"] or "",
                "dates": [],
                "qtys": [],
            },
        )
        day = _parse_day(r["invoice_date"])
        if day:
            rec["dates"].append(day)
            rec["qtys"].append(float(r["qty"] or 0))
        if r["official_name"]:
            rec["official_name"] = r["official_name"]
    return out


def _likely_score(
    *,
    times: int,
    gaps: int,
    weighted: float,
    cv: float,
    elapsed: int | None,
    reason: str,
    excluded: bool,
) -> int:
    """Continuous 0-100. Filters are penalties, not a hard zero."""
    if excluded or reason == "noise":
        return 0
    if times < 2 or gaps < 1 or not weighted:
        return 0

    ratio = (elapsed or 0) / max(weighted, 1.0)
    urgency = 100.0 / (1.0 + math.exp(-2.8 * (ratio - 0.85)))
    # long cycles never outrank weekly staples just because they are "very overdue"
    urgency *= min(1.0, 28.0 / max(weighted, 1.0))
    regularity = 1.0 / (1.0 + max(0.0, cv))
    support = min(1.0, math.log(1.0 + times) / math.log(13.0))

    pen = 1.0
    if reason == "occasional_small_pack":
        pen *= 0.18
    elif reason == "interval_too_long":
        pen *= max(0.28, min(1.0, 45.0 / weighted))
    elif reason == "irregular":
        pen *= 0.55
    elif reason == "lapsed":
        extra = max(0, (elapsed or 0) - 90)
        pen *= max(0.2, 0.7 - extra / 240.0)
    elif reason == "too_few_buys":
        pen *= 0.45

    raw = urgency * (0.4 + 0.6 * regularity) * (0.45 + 0.55 * support) * pen
    return int(round(max(0.0, min(100.0, raw))))


def classify(
    rec: dict[str, Any],
    *,
    today: date,
    min_buys: int,
    max_interval_days: float,
    max_cv: float,
    ewma_alpha: float,
    lapsed_factor: float,
    max_last_age_days: int,
    excluded: bool,
    vote: str = "",
) -> dict[str, Any]:
    official = rec.get("official_name") or ""
    receipt = rec.get("receipt_name") or ""
    name = official or receipt
    dates: list[date] = list(rec.get("dates") or [])
    unique_days = sorted(set(dates))
    times = len(unique_days)
    last = unique_days[-1] if unique_days else None
    first = unique_days[0] if unique_days else None
    gaps = successive_intervals(unique_days)
    mu, sd = mean_std([float(g) for g in gaps])
    weighted = ewma([float(g) for g in gaps], ewma_alpha) if gaps else 0.0
    cv = (sd / mu) if mu else (10.0 if times >= 2 else 0.0)
    elapsed = (today - last).days if last else None
    due_in = None
    if last and weighted:
        nxt = last + timedelta(days=max(1, round(weighted)))
        due_in = (nxt - today).days
        next_due = nxt.isoformat()
    else:
        next_due = ""
    pack = pack_reason(name) or pack_reason(receipt)

    reason = "unknown"
    include = False
    if excluded:
        reason = "excluded"
    elif any(bit in name.lower() for bit in SKIP_NAME) or any(bit in receipt.lower() for bit in SKIP_NAME):
        reason = "noise"
    elif times < 2 or not gaps:
        reason = "too_few_buys"
    elif pack:
        reason = pack
    elif weighted > max_interval_days:
        reason = "interval_too_long"
    elif cv > max_cv:
        reason = "irregular"
    elif last and elapsed is not None and (
        elapsed > max_last_age_days or elapsed > lapsed_factor * max(weighted, 1)
    ):
        reason = "lapsed"
    else:
        include = True
        if due_in is not None and due_in < 0:
            reason = "regular_overdue"
        elif due_in == 0:
            reason = "regular_due_today"
        elif due_in == 1:
            reason = "regular_due_tomorrow"
        else:
            reason = "regular_upcoming"

    score = _likely_score(
        times=times,
        gaps=len(gaps),
        weighted=weighted,
        cv=cv,
        elapsed=elapsed,
        reason=reason,
        excluded=excluded,
    )

    status = {
        "regular_overdue": "overdue",
        "regular_due_today": "due_today",
        "regular_due_tomorrow": "due_tomorrow",
        "regular_upcoming": "upcoming",
        "lapsed": "lapsed",
        "excluded": "excluded",
    }.get(reason, "unknown")

    out = {
        "key": rec.get("key"),
        "name": name,
        "receipt_name": receipt,
        "official_name": official,
        "barcode": rec.get("barcode") or "",
        "dept": classify_dept(receipt, official),
        "times_bought": times,
        "first_buy": first.isoformat() if first else "",
        "last_buy": last.isoformat() if last else "",
        "days_since": elapsed,
        "mean_interval_days": round(mu, 1) if gaps else None,
        "std_interval_days": round(sd, 1) if gaps else None,
        "weighted_interval_days": round(weighted, 1) if gaps else None,
        "cv": round(cv, 2) if gaps else None,
        "interval_days": round(weighted, 1) if gaps else 10**9,
        "next_due": next_due,
        "due_in_days": due_in,
        "score": score,
        "reason": reason,
        "include": include,
        "status": status,
        "frequency": f"every {weighted:.1f} days" if weighted else "—",
    }
    return apply_vote(out, vote)


def forecast(
    user_id: int,
    *,
    horizon_days: int = 7,
    today: date | None = None,
    dept: str = "",
    min_buys: int | None = None,
    max_interval_days: float | None = None,
    exclude: list[str] | None = None,
    include_excluded: bool = False,
) -> list[dict[str, Any]]:
    today = today or date.today()
    cfg = dict(DEFAULTS)
    if min_buys is not None:
        cfg["min_buys"] = int(min_buys)
    if max_interval_days is not None:
        cfg["max_interval_days"] = float(max_interval_days)
    blocked = load_exclusions(user_id)
    votes = load_votes(user_id)
    extra = {e.strip().lower() for e in (exclude or []) if e and e.strip()}
    hist = buy_history(user_id, until=today)
    out = []
    want_dept = normalize_dept(dept)
    for rec in hist.values():
        name = (rec.get("official_name") or rec.get("receipt_name") or "")
        excluded = rec["key"] in blocked or rec["key"].lower() in extra or name.lower() in extra
        row = classify(rec, today=today, excluded=excluded, vote=votes.get(rec["key"], ""), **{k: cfg[k] for k in (
            "min_buys", "max_interval_days", "max_cv", "ewma_alpha", "lapsed_factor", "max_last_age_days"
        )})
        if want_dept and row.get("dept") != want_dept:
            continue
        if not include_excluded and not row["include"]:
            continue
        out.append(row)
    out.sort(key=lambda p: (-int(p.get("score") or 0), int(p.get("due_in_days") if p.get("due_in_days") is not None else 10**6)))
    horizon = max(0, int(horizon_days))
    for p in out:
        p["in_horizon"] = p.get("due_in_days") is not None and p["due_in_days"] <= horizon
    return out
