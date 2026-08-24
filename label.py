#!/usr/bin/env python3
"""label.py — the dead-simple self-labeling log (GOAL 4).

Records what I actually did / how I felt, time-aligned to the ring's raw stream,
into the derived store (db/metrics.db, `labels` table). This is the CLI precursor
to the app's Journal surface — the point is to **start accruing ground truth now**
so there's a private training set when it's time to teach the ring to detect
workouts. Design: LABELING.md · schema: db/schema.sql. Stdlib only — no venv.

  # the two-second paths
  ./label.py quick run                  # a run, starting now
  ./label.py quick cycling 2-3pm        # cycling, 2–3pm today
  ./label.py quick lift --for 45m -i hard
  ./label.py log "wine at dinner"       # free-text note (parsed later by the LLM)

  # look at what's accruing
  ./label.py recent                     # last 15
  ./label.py list --kind activity --since 7d
  ./label.py stats                      # dataset growing? counts by kind/source

  # fix things (corrections are themselves good training signal)
  ./label.py show 12
  ./label.py edit 12 --intensity moderate --category run
  ./label.py rm 12

  # full control (scripts / power use)
  ./label.py add --kind symptom --category sick --start 9am --end 5pm -t "sore throat"
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / "db" / "metrics.db"
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"

KINDS = ("activity", "context", "symptom", "note", "correction")
SOURCES = ("typed", "quick-add", "question", "auto")


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open metrics.db, applying db/schema.sql idempotently so the labels table
    always exists (zero-setup)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    else:  # self-sufficient fallback if the schema file is missing
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS labels ("
            "id INTEGER PRIMARY KEY, start_ts INTEGER NOT NULL, end_ts INTEGER,"
            "kind TEXT NOT NULL DEFAULT 'note', category TEXT, text TEXT,"
            "tags TEXT NOT NULL DEFAULT '[]', intensity TEXT,"
            "source TEXT NOT NULL DEFAULT 'typed', confidence REAL DEFAULT 1.0,"
            "created_unix INTEGER NOT NULL);"
        )
    return conn


def now_unix() -> int:
    return int(datetime.now().timestamp())


# --------------------------------------------------------------------------- #
# Time parsing — pragmatic, never fatal. Rich NL ("yesterday", "2–3pm after
# lunch") is the future local-LLM's job; here we handle the common clock forms
# and fall back to "now" (with a warning) rather than ever lose a label.
# --------------------------------------------------------------------------- #
_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)


def _clock_to_unix(token: str, base: datetime, default_meridiem: str | None):
    """Parse a single clock token ('2', '2:30', '2pm', '14:00') against `base`'s
    date. Returns (unix:int, meridiem:str|None) or None if it isn't a clock."""
    m = _CLOCK_RE.match(token.strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or default_meridiem or "").lower() or None
    if hour > 23 or minute > 59:
        return None
    if meridiem == "am":
        hour = 0 if hour == 12 else hour
    elif meridiem == "pm":
        hour = 12 if hour == 12 else (hour + 12 if hour < 12 else hour)
    dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(dt.timestamp()), meridiem


def parse_time(token: str, base: datetime | None = None) -> int | None:
    """A single instant → unix sec. Handles now / unix / ISO / clock. None if unparseable."""
    base = base or datetime.now()
    t = token.strip().lower()
    if t in ("now", ""):
        return now_unix()
    if t.isdigit() and int(t) > 100_000:       # a real unix timestamp, not a bare hour
        return int(t)
    clock = _clock_to_unix(t, base, None)
    if clock:
        return clock[0]
    try:                                        # ISO: 2026-08-05, 2026-08-05T14:00
        return int(datetime.fromisoformat(token.strip()).timestamp())
    except ValueError:
        return None


def parse_range(spec: str, base: datetime | None = None):
    """A time spec → (start_ts, end_ts|None, ok). Handles 'A-B' ranges (with
    shared meridiem, e.g. '2-3pm') and single instants. ok=False => couldn't
    parse; caller should fall back to now and keep the raw text."""
    base = base or datetime.now()
    s = spec.replace("–", "-").replace("—", "-").strip()

    # Range "A-B" (guard against a leading-negative unix, unlikely but cheap).
    if "-" in s and not s.lstrip().startswith("-"):
        left, right = (p.strip() for p in s.split("-", 1))
        r = _clock_to_unix(right, base, None)               # parse end first for its meridiem
        if r:
            end_unix, end_mer = r
            l = _clock_to_unix(left, base, end_mer)          # end's meridiem defaults the start
            if l:
                start_unix = l[0]
                if end_unix <= start_unix:                   # crossed midnight (e.g. 11pm-1am)
                    end_unix += 86_400
                return start_unix, end_unix, True
        # Not a clock range — maybe two ISO/unix instants.
        ls, rs = parse_time(left, base), parse_time(right, base)
        if ls is not None and rs is not None:
            return ls, (rs if rs > ls else rs + 86_400), True
        return now_unix(), None, False

    single = parse_time(s, base)
    if single is not None:
        return single, None, True
    return now_unix(), None, False


def parse_duration(spec: str) -> int | None:
    """'45m' / '1h' / '1h30m' / '90m' / '2h' → seconds. None if unparseable."""
    total, found = 0, False
    for value, unit in re.findall(r"(\d+)\s*([hm])", spec.strip().lower()):
        total += int(value) * (3600 if unit == "h" else 60)
        found = True
    return total if found else None


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def fmt_clock(unix: int) -> str:
    dt = datetime.fromtimestamp(unix)
    h = dt.strftime("%I:%M%p").lstrip("0").lower()
    return h


def fmt_day(unix: int) -> str:
    dt = datetime.fromtimestamp(unix).date()
    today = datetime.now().date()
    if dt == today:
        return "today"
    if dt == today - timedelta(days=1):
        return "yesterday"
    return dt.strftime("%b %-d")


def fmt_span(start: int, end: int | None) -> str:
    if end is None:
        return f"{fmt_day(start)} {fmt_clock(start)}"
    mins = round((end - start) / 60)
    dur = f"{mins}m" if mins < 60 else f"{mins // 60}h{mins % 60:02d}m"
    same_day = datetime.fromtimestamp(start).date() == datetime.fromtimestamp(end).date()
    tail = fmt_clock(end) if same_day else f"{fmt_day(end)} {fmt_clock(end)}"
    return f"{fmt_day(start)} {fmt_clock(start)}–{tail} ({dur})"


def fmt_ago(unix: int) -> str:
    secs = now_unix() - unix
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86_400}d ago"


# --------------------------------------------------------------------------- #
# Insert / query
# --------------------------------------------------------------------------- #
def insert_label(conn, *, start_ts, end_ts, kind, category, text, tags,
                 intensity, source, confidence) -> int:
    cur = conn.execute(
        "INSERT INTO labels (start_ts,end_ts,kind,category,text,tags,intensity,"
        "source,confidence,created_unix) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (start_ts, end_ts, kind, category, text, json.dumps(tags or []),
         intensity, source, confidence, now_unix()),
    )
    conn.commit()
    return cur.lastrowid


def print_row(row: sqlite3.Row, *, wide: bool = False) -> None:
    cat = row["category"] or "—"
    span = fmt_span(row["start_ts"], row["end_ts"])
    ico = {"activity": "🏃", "context": "•", "symptom": "🤒",
           "note": "✎", "correction": "↺"}.get(row["kind"], "•")
    line = f"  {row['id']:>3} {ico} {cat:<12} {span:<28} {fmt_ago(row['created_unix'])}"
    extras = []
    if row["intensity"]:
        extras.append(f"intensity={row['intensity']}")
    if row["source"] != "typed":
        extras.append(row["source"])
    if row["confidence"] is not None and row["confidence"] < 1.0:
        extras.append(f"conf={row['confidence']:.2f}")
    if extras:
        line += "  [" + " ".join(extras) + "]"
    print(line)
    if row["text"]:
        print(f"        “{row['text']}”")
    if wide:
        tags = json.loads(row["tags"] or "[]")
        if tags:
            print(f"        tags: {', '.join(tags)}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_quick(conn, args) -> int:
    """~2-tap activity log: category [+ time/duration]."""
    start, end, ok = now_unix(), None, True
    warn = None
    if args.for_:
        dur = parse_duration(args.for_)
        if dur is None:
            warn = f"couldn't parse duration {args.for_!r}"
        else:
            end = start + dur
    if args.when:
        start, end2, ok = parse_range(args.when)
        if end is None:
            end = end2
        if not ok:
            warn = f"couldn't parse time {args.when!r} — logged at now"
    text = args.note
    if warn:
        text = (text + " | " if text else "") + f"[unparsed: {args.when or args.for_}]"
        print(f"  ⚠️  {warn}")
    lid = insert_label(
        conn, start_ts=start, end_ts=end, kind=args.kind, category=args.category,
        text=text, tags=args.tag, intensity=args.intensity,
        source="quick-add", confidence=1.0,
    )
    print(f"  ✓ #{lid} logged {args.category}  {fmt_span(start, end)}")
    return 0


def cmd_log(conn, args) -> int:
    """Free-text note; the local LLM will parse it into structure later."""
    start = now_unix()
    if args.at:
        start, _, ok = parse_range(args.at)
        if not ok:
            print(f"  ⚠️  couldn't parse time {args.at!r} — logged at now")
            start = now_unix()
    lid = insert_label(
        conn, start_ts=start, end_ts=None, kind=args.kind, category=args.category,
        text=args.text, tags=args.tag, intensity=None,
        source="typed", confidence=1.0,
    )
    print(f"  ✓ #{lid} noted  {fmt_span(start, None)}: “{args.text}”")
    return 0


def cmd_add(conn, args) -> int:
    """Full structured add (scripts / power use)."""
    start, end, ok = parse_range(args.start)
    if not ok:
        print(f"  ⚠️  couldn't parse --start {args.start!r} — using now")
        start = now_unix()
    if args.end:
        end = parse_time(args.end)
        if end is None:
            print(f"  ⚠️  couldn't parse --end {args.end!r} — leaving open")
    if args.for_ and end is None:
        dur = parse_duration(args.for_)
        if dur is not None:
            end = start + dur
    lid = insert_label(
        conn, start_ts=start, end_ts=end, kind=args.kind, category=args.category,
        text=args.text, tags=args.tag, intensity=args.intensity,
        source=args.source, confidence=args.confidence,
    )
    print(f"  ✓ #{lid} {args.kind} {args.category or ''}  {fmt_span(start, end)}".rstrip())
    return 0


def _since_to_unix(spec: str | None) -> int | None:
    if not spec:
        return None
    m = re.match(r"^(\d+)d$", spec.strip())      # '7d' — days back
    if m:
        return now_unix() - int(m.group(1)) * 86_400
    dur = parse_duration(spec)                    # '24h', '90m' — h/m back
    if dur is not None:
        return now_unix() - dur
    return parse_time(spec)                        # else an absolute date/time


def cmd_list(conn, args) -> int:
    where, params = [], []
    if getattr(args, "kind", None):
        where.append("kind = ?"); params.append(args.kind)
    if getattr(args, "category", None):
        where.append("category = ?"); params.append(args.category)
    if getattr(args, "source", None):
        where.append("source = ?"); params.append(args.source)
    since = _since_to_unix(getattr(args, "since", None))
    if since is not None:
        where.append("start_ts >= ?"); params.append(since)
    sql = "SELECT * FROM labels"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY start_ts DESC LIMIT ?"
    params.append(args.n)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("  (no labels yet — try:  ./label.py quick run)")
        return 0
    print(f"  {len(rows)} label(s):")
    for row in rows:
        print_row(row, wide=args.wide)
    return 0


def cmd_show(conn, args) -> int:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"  no label #{args.id}")
        return 1
    print_row(row, wide=True)
    print(f"        created {datetime.fromtimestamp(row['created_unix']):%Y-%m-%d %H:%M} "
          f"· kind={row['kind']} · source={row['source']}")
    return 0


def cmd_edit(conn, args) -> int:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"  no label #{args.id}")
        return 1
    sets, params = [], []
    if args.category is not None:
        sets.append("category = ?"); params.append(args.category)
    if args.kind is not None:
        sets.append("kind = ?"); params.append(args.kind)
    if args.intensity is not None:
        sets.append("intensity = ?"); params.append(args.intensity)
    if args.text is not None:
        sets.append("text = ?"); params.append(args.text)
    if args.tag is not None:
        sets.append("tags = ?"); params.append(json.dumps(args.tag))
    if args.start is not None:
        s, _, ok = parse_range(args.start)
        if ok:
            sets.append("start_ts = ?"); params.append(s)
    if args.end is not None:
        e = parse_time(args.end)
        if e is not None:
            sets.append("end_ts = ?"); params.append(e)
    if not sets:
        print("  nothing to change (pass --category/--kind/--intensity/--text/--tag/--start/--end)")
        return 1
    params.append(args.id)
    conn.execute(f"UPDATE labels SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    print_row(conn.execute("SELECT * FROM labels WHERE id = ?", (args.id,)).fetchone(), wide=True)
    return 0


def cmd_rm(conn, args) -> int:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"  no label #{args.id}")
        return 1
    if not args.yes:
        print_row(row)
        if sys.stdin.isatty():
            if input("  delete this? [y/N] ").strip().lower() not in ("y", "yes"):
                print("  kept.")
                return 0
        else:
            print("  refusing to delete non-interactively without --yes")
            return 1
    conn.execute("DELETE FROM labels WHERE id = ?", (args.id,))
    conn.commit()
    print(f"  ✓ deleted #{args.id}")
    return 0


def cmd_stats(conn, args) -> int:
    total = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    if not total:
        print("  no labels yet — the dataset starts with your first  ./label.py quick <thing>")
        return 0
    span = conn.execute("SELECT MIN(start_ts), MAX(start_ts) FROM labels").fetchone()
    days = max(1, round((span[1] - span[0]) / 86_400)) if span[0] else 1
    print(f"  {total} labels over ~{days} day(s)  (~{total / days:.1f}/day)")
    for col, title in (("kind", "by kind"), ("source", "by source"), ("category", "top categories")):
        rows = conn.execute(
            f"SELECT COALESCE({col},'—') k, COUNT(*) c FROM labels GROUP BY k "
            f"ORDER BY c DESC LIMIT 8"
        ).fetchall()
        cells = "  ".join(f"{r['k']}:{r['c']}" for r in rows)
        print(f"  {title:<16} {cells}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="label.py", description="Self-labeling log for oura-local (GOAL 4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=str(DB_PATH), help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quick", help="~2-tap activity log: category [time]")
    q.add_argument("category", help="e.g. run, cycling, lift, walk")
    q.add_argument("when", nargs="?", help="time or range: 'now', '2-3pm', '14:00-15:00'")
    q.add_argument("--kind", choices=KINDS, default="activity")
    q.add_argument("--for", dest="for_", metavar="DUR", help="duration, e.g. 45m, 1h30m")
    q.add_argument("-i", "--intensity", help="easy|moderate|hard or 1-10")
    q.add_argument("-t", "--tag", action="append", help="add a tag (repeatable)")
    q.add_argument("--note", help="optional free text")
    q.set_defaults(func=cmd_quick)

    lg = sub.add_parser("log", help="free-text note (parsed by the LLM later)")
    lg.add_argument("text", help="what you did / how you felt, in your words")
    lg.add_argument("--at", help="when (default now): '2pm', '14:00', ISO")
    lg.add_argument("--kind", choices=KINDS, default="note")
    lg.add_argument("--category", help="optional category hint")
    lg.add_argument("-t", "--tag", action="append", help="add a tag (repeatable)")
    lg.set_defaults(func=cmd_log)

    a = sub.add_parser("add", help="full structured add")
    a.add_argument("--kind", choices=KINDS, default="activity")
    a.add_argument("--category")
    a.add_argument("--start", default="now", help="time or range")
    a.add_argument("--end", help="explicit end time")
    a.add_argument("--for", dest="for_", metavar="DUR", help="duration if no --end")
    a.add_argument("-t", "--text", help="free text")
    a.add_argument("--tag", action="append", help="tag (repeatable)")
    a.add_argument("-i", "--intensity")
    a.add_argument("--source", choices=SOURCES, default="typed")
    a.add_argument("--confidence", type=float, default=1.0)
    a.set_defaults(func=cmd_add)

    for name, helptext in (("recent", "last N labels (default 15)"),
                           ("list", "list labels with filters")):
        l = sub.add_parser(name, help=helptext)
        l.add_argument("n", nargs="?", type=int, default=15, help="how many")
        l.add_argument("--kind", choices=KINDS)
        l.add_argument("--category")
        l.add_argument("--source", choices=SOURCES)
        l.add_argument("--since", help="e.g. 7d, 24h, or a date")
        l.add_argument("--wide", action="store_true", help="show tags too")
        l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="one label in detail")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    e = sub.add_parser("edit", help="edit a label (corrections are good signal)")
    e.add_argument("id", type=int)
    e.add_argument("--category"); e.add_argument("--kind", choices=KINDS)
    e.add_argument("--intensity"); e.add_argument("--text")
    e.add_argument("--tag", action="append")
    e.add_argument("--start"); e.add_argument("--end")
    e.set_defaults(func=cmd_edit)

    r = sub.add_parser("rm", help="delete a label")
    r.add_argument("id", type=int)
    r.add_argument("--yes", action="store_true", help="skip confirmation")
    r.set_defaults(func=cmd_rm)

    st = sub.add_parser("stats", help="dataset overview (is it growing?)")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect(Path(args.db))
    try:
        return args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
