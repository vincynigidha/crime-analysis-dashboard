"""
Crime forecast built ONLY from the cases stored in the Crime Records table
(plus the linked-series details from Case Linkage). No external dataset.

For every (area, category) pair it estimates how many crimes to expect in the
next 30 days:

  1. RECORD RATE   recency-weighted crimes per day from that pair's own past cases
                   (a case's weight halves every HALF_LIFE_DAYS days).
  2. SERIES RATE   if Case Linkage found an ACTIVE series of the same category that
                   is likely to hit this area, the series' own pace
                   (30 / typical gap days x the area's share) is used when higher.

  expected_30 = max(record rate x 30, series expectation)

Small data (e.g. 29 cases) means these are rough planning estimates. A backtest on
the most recent 30 days is included so the accuracy is visible, not assumed.
"""
from collections import defaultdict
from datetime import timedelta
from math import log

HALF_LIFE_DAYS = 30
HORIZON_DAYS = 30


def _record_rate(dates, as_of, first_date):
    """Recency-weighted cases/day. Normalised so a steady stream gives its true rate."""
    span = max(HORIZON_DAYS, (as_of - first_date).days + 1)
    kernel_area = (HALF_LIFE_DAYS / log(2)) * (1 - 0.5 ** (span / HALF_LIFE_DAYS))
    weighted = sum(0.5 ** (max(0, (as_of - d).days) / HALF_LIFE_DAYS) for d in dates)
    return weighted / kernel_area


def _risk(expected):
    return "High" if expected >= 1.0 else "Medium" if expected >= 0.4 else "Low"


def _active_series_expectations(clusters, as_of):
    """{(area, category): (expected_next_30, series_name)} from series still active."""
    out = {}
    for c in clusters:
        f = c.get("forecast")
        if not f:
            continue
        last = max(m["reported_date"] for m in c["members"])
        gap = max(1, f["avg_gap_days"])
        # active = last incident is within two typical gaps (and at most 60 days) of as_of
        if (as_of - last).days > min(60, gap * 2 + 14):
            continue
        pace = HORIZON_DAYS / gap
        for p in f["places"]:
            key = (p["area"], c["category_name"])
            exp = pace * p["share"] / 100.0
            if key not in out or exp > out[key][0]:
                out[key] = (exp, c.get("series_name", "series"))
    return out


def _group_for_cell(clusters, area, category):
    """The linked series (crime group) most tied to this area + category, with how many
    of its cases are already solved. Returns None if no series fits."""
    best, best_key = None, None
    for c in clusters or []:
        if c["category_name"] != category:
            continue
        in_members = any(m["area_name"] == area for m in c["members"])
        f = c.get("forecast") or {}
        share = next((p["share"] for p in f.get("places", []) if p["area"] == area), 0)
        if not in_members and not share:
            continue
        key = (in_members, share, c["size"])
        if best_key is None or key > best_key:
            best, best_key = c, key
    if best is None:
        return None
    solved = sum(1 for m in best["members"] if m.get("status") == "Solved")
    return {"name": best.get("series_name", "series"), "size": best["size"],
            "solved": solved, "past_here": best_key[0]}


def predict_cells(records, clusters, as_of):
    """records: dicts with reported_date, area_name, category_name (all non-null).
    Returns list of dicts sorted by expected count, highest first."""
    by_cell = defaultdict(list)
    for r in records:
        if r["reported_date"] <= as_of:
            by_cell[(r["area_name"], r["category_name"])].append(r["reported_date"])
    if not by_cell:
        return []
    first = min(d for ds in by_cell.values() for d in ds)
    series = _active_series_expectations(clusters, as_of) if clusters else {}

    keys = set(by_cell) | set(series)
    cells = []
    for key in keys:
        dates = by_cell.get(key, [])
        rec = _record_rate(dates, as_of, first) * HORIZON_DAYS if dates else 0.0
        ser, sname = series.get(key, (0.0, None))
        expected = max(rec, ser)
        used_series = sname is not None and ser > rec
        cells.append({
            "area": key[0], "category": key[1],
            "past_cases": len(dates),
            "last_case": max(dates) if dates else None,
            "record_expected": rec,
            "series_expected": ser if sname else None,
            "series_name": sname,
            "used_series": used_series,
            "group": _group_for_cell(clusters, key[0], key[1]),
            "expected": expected,
            "risk": _risk(expected),
        })
    cells.sort(key=lambda c: (-c["expected"], c["area"], c["category"]))
    return cells


def backtest(records, as_of):
    """Pretend it is (as_of - 30 days): predict from earlier cases only (no series info),
    then compare with what really happened in the following 30 days."""
    cutoff = as_of - timedelta(days=HORIZON_DAYS)
    train = [r for r in records if r["reported_date"] <= cutoff]
    if len(train) < 5:
        return None
    preds = {(c["area"], c["category"]): c["expected"] for c in predict_cells(train, [], cutoff)}
    actual = defaultdict(int)
    for r in records:
        if cutoff < r["reported_date"] <= as_of:
            actual[(r["area_name"], r["category_name"])] += 1
    keys = set(preds) | set(actual)
    if not keys:
        return None
    mae = sum(abs(preds.get(k, 0) - actual.get(k, 0)) for k in keys) / len(keys)
    naive = sum(actual.get(k, 0) for k in keys) / len(keys)   # error if we always predicted 0
    return {
        "mae": round(mae, 2),
        "naive_mae": round(naive, 2),
        "predicted_total": round(sum(preds.values()), 1),
        "actual_total": sum(actual.values()),
        "cells": len(keys),
        "train_cases": len(train),
    }
