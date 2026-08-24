#!/usr/bin/env python3
"""Estimate VO2max from a Tier-2 guided walk test — Oura's own recipe, reproduced.

Oura's "Cardio Capacity" is not one of the ring's on-device models; it's computed app-side
(training_stress_score consumes it as an external input). So "use Oura's method" =
reproduce their math. This interpreter does the Tier-2 (guided 6-min brisk walk)
point estimate and writes it as a first-class test into db/metrics.db.

Recipe (see memory signet-vo2max-recipe / interpret/STEP_MAPPING.md neighbours):
  • point estimate = Burr 2011 6-minute-walk-test regression (R^2 ~= 0.72):
        VO2max = 70.161 + 0.023*dist6_m - 0.276*wt_kg - 6.79*sex(M=0,F=1)
                        - 0.193*rest_hr - 0.191*age
    dist6_m = distance covered in SIX minutes (GPS). A longer steady walk is scaled
    to 6 min (constant-pace assumption; the ring/Strava confirmed steady cadence).
  • max HR   = Tanaka 208 - 0.7*age  (Oura's own constant, from the energy model).
  • category = Oura's REAL vo2max_thresholds age/sex cut-points.
  • corroborators (printed, not stored): Jackson demographic (Tier-1) + the walk's
    own exercise-HR plateau when PPG density allows (motion usually kills it).

Resting HR comes from the ring's overnight RHR (baselines.rhr, else median daily
rhr). NOTE: Burr's resting HR was seated; overnight RHR runs ~5-10 bpm lower, so the
overnight-RHR estimate is a mild upper bound — pass --rest-hr to override.

    .venv/bin/python interpret/vo2max.py --dist-mi 0.40 --duration-s 483 \
        --window-start "2026-08-08 19:41:38" --window-end "2026-08-08 19:49:41"
"""
import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OURA_DB = REPO / "oura.db"
METRICS_DB = REPO / "db" / "metrics.db"

TAG_IBI_GREEN = 128  # 0x80 green_ibi_quality (quality-gated HR)

# Oura's real vo2max_thresholds — 3 cut-points per (sex, age-band) => 4 categories.
# Partial table (rows sourced so far); extend as more rows are recovered.
CATS = ["Below average", "Average", "Good", "Excellent"]
VO2_THRESHOLDS = {
    ("M", 25): [36, 45, 53],   # male 25-29
    ("F", 40): [26, 31, 37],   # female 40-44
}


def band(age):
    return max(20, min(80, (age // 5) * 5))


def categorize(vo2, sex, age):
    cuts = VO2_THRESHOLDS.get((sex, band(age)))
    if not cuts:
        return None, None
    i = int(np.searchsorted(cuts, vo2, side="right"))
    return CATS[i], cuts


def profile(metrics_db):
    con = sqlite3.connect(str(metrics_db))
    p = dict(con.execute("SELECT key, value FROM prefs").fetchall())
    con.close()
    return (int(float(p["age"])), p.get("sex", "M").upper().startswith("F"),
            float(p["weight_kg"]))


def resting_hr(metrics_db, override):
    if override is not None:
        return float(override), "override"
    con = sqlite3.connect(str(metrics_db))
    row = con.execute("SELECT baseline FROM baselines WHERE metric='rhr'").fetchone()
    if row and row[0]:
        con.close()
        return float(row[0]), "overnight RHR baseline"
    row = con.execute("SELECT rhr FROM daily_metrics WHERE rhr IS NOT NULL "
                      "ORDER BY date DESC").fetchall()
    con.close()
    if row:
        return float(np.median([r[0] for r in row])), "median daily RHR"
    sys.exit("error: no resting HR available (baselines/daily_metrics)")


def ring_ds_to_unix(con):
    rows = con.execute("SELECT ring_timestamp, captured_unix FROM events").fetchall()
    rt = np.asarray([r[0] for r in rows], np.int64)
    cu = np.asarray([r[1] for r in rows], np.int64)
    k = int(np.argmax(rt))
    mx, anchor = int(rt[k]), int(cu[k])
    return lambda ds: anchor - (mx - ds) / 10.0


def parse_when(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return datetime.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").timestamp()


def walk_hr(oura_db, ws, we):
    """Quality-gated HR beats in [ws,we]; report density + plateau (last 3 min)."""
    con = sqlite3.connect(f"file:{oura_db}?mode=ro", uri=True)
    to_unix = ring_ds_to_unix(con)
    beats = []
    for ds, js in con.execute(
            "SELECT ring_timestamp, decoded_json FROM events WHERE tag=? "
            "AND decoded_json IS NOT NULL ORDER BY ring_timestamp", (TAG_IBI_GREEN,)):
        u = to_unix(ds)
        if not (ws - 30 <= u <= we + 30):
            continue
        v = json.loads(js)
        ibis, q = v.get("ibi_ms") or [], v.get("quality") or []
        for i, ibi in enumerate(ibis):
            if ibi and (q[i] if i < len(q) else 0) >= 3 and 300 < ibi < 1500:
                beats.append((u, 60000.0 / ibi))
    con.close()
    b = np.array(beats)
    if not len(b):
        return {"n": 0}
    inw = b[(b[:, 0] >= ws) & (b[:, 0] <= we), 1]
    plat = b[(b[:, 0] >= we - 180) & (b[:, 0] <= we), 1]
    return {"n": len(inw), "median": float(np.median(inw)) if len(inw) else None,
            "max": float(inw.max()) if len(inw) else None,
            "plateau_n": len(plat),
            "plateau": float(np.median(plat)) if len(plat) else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_OURA_DB))
    ap.add_argument("--metrics-db", default=str(METRICS_DB))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dist-m", type=float, help="GPS distance walked (metres)")
    g.add_argument("--dist-mi", type=float, help="GPS distance walked (miles)")
    ap.add_argument("--duration-s", type=float, help="walk duration (s); scales dist to 6 min")
    ap.add_argument("--window-start", help="'YYYY-MM-DD HH:MM:SS' local (for HR + date)")
    ap.add_argument("--window-end", help="'YYYY-MM-DD HH:MM:SS' local (for HR)")
    ap.add_argument("--rest-hr", type=float, help="override resting HR (bpm)")
    ap.add_argument("--date", help="test date YYYY-MM-DD (default: from window-start / today)")
    ap.add_argument("--dry-run", action="store_true", help="don't write metrics.db")
    args = ap.parse_args()

    age, female, wt = profile(args.metrics_db)
    sex = "F" if female else "M"
    rest_hr, rhr_src = resting_hr(args.metrics_db, args.rest_hr)

    dist_total = args.dist_m if args.dist_m is not None else args.dist_mi * 1609.344
    dur = args.duration_s or 360.0
    dist6 = dist_total * (360.0 / dur)          # scale to 6 min (steady-pace assumption)

    # ── Burr 2011 6MWT point estimate ──
    vo2 = (70.161 + 0.023 * dist6 - 0.276 * wt - 6.79 * (1 if female else 0)
           - 0.193 * rest_hr - 0.191 * age)
    max_hr = 208 - 0.7 * age
    cat, cuts = categorize(vo2, sex, age)

    # corroborators
    jackson = 79.9 - 0.39 * age - 13.7 * (1 if female else 0) - 0.127 * (wt * 2.20462)

    date = args.date
    if not date and args.window_start:
        date = datetime.datetime.fromtimestamp(parse_when(args.window_start)).strftime("%Y-%m-%d")
    if not date:
        date = datetime.date.today().isoformat()

    print("VO2max — Tier-2 6-min walk test (Burr 2011), Oura's recipe\n")
    print(f"  profile:        {age}{sex}, {wt:.1f} kg   resting HR {rest_hr:.0f} bpm ({rhr_src})")
    print(f"  walk:           {dist_total:.0f} m in {dur/60:.1f} min "
          f"=> 6-min distance {dist6:.0f} m ({dist6/1609.344*5280/6:.0f} ft/min)")
    print(f"\n  >>> VO2max = {vo2:.1f} ml/kg/min", end="")
    if cat:
        print(f"   [{cat}]  (band cut-points {cuts})")
    else:
        print()
    print(f"      Tanaka max HR = {max_hr:.0f} bpm")
    print(f"\n  corroborators:  Jackson demographic (Tier-1) = {jackson:.1f}")

    hr = walk_hr(args.db, parse_when(args.window_start), parse_when(args.window_end)) \
        if args.window_start and args.window_end else {"n": 0}
    if hr["n"]:
        print(f"  walk HR:        {hr['n']} quality beats  median {hr['median']:.0f}  max {hr['max']:.0f} bpm")
        if hr["plateau"]:
            print(f"                  plateau (last 3 min, n={hr['plateau_n']}) {hr['plateau']:.0f} bpm "
                  f"= {hr['plateau']/max_hr*100:.0f}% max HR")
        else:
            print("                  too sparse for a plateau (PPG motion artifact) — HR layer skipped")
    elif args.window_start:
        print("  walk HR:        no quality beats in window (PPG motion artifact)")

    if args.dry_run:
        print("\n  [dry-run] not writing metrics.db")
        return

    note = (f"flat 6MWT (Burr): {dist6:.0f} m/6min, restHR {rest_hr:.0f} ({rhr_src}); "
            f"{cat or 'uncategorized'}; corrob Jackson {jackson:.0f}")
    con = sqlite3.connect(str(args.metrics_db))
    con.execute("CREATE TABLE IF NOT EXISTS vo2_tests(date TEXT, vo2max REAL, method TEXT, "
                "note TEXT, created_unix INTEGER)")
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    con.execute("DELETE FROM vo2_tests WHERE date=? AND method LIKE '6MWT%'", (date,))
    con.execute("INSERT INTO vo2_tests(date, vo2max, method, note, created_unix) VALUES(?,?,?,?,?)",
                (date, round(vo2, 1), "6MWT (Burr) flat walk", note, now))
    try:
        con.execute("UPDATE daily_metrics SET vo2max=? WHERE date=?", (round(vo2, 1), date))
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()
    print(f"\n  wrote vo2_tests[{date}] = {vo2:.1f}  (method '6MWT (Burr) flat walk')")


if __name__ == "__main__":
    main()
