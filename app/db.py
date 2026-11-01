"""Data layer: SQLite schema, password hashing, and the streak/adherence maths.

Kept separate from main.py so that business rules (what counts as a streak?)
can be unit-tested without HTTP, and the web layer stays thin.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DATA = Path(os.environ.get("GYMBRO_DATA", Path(__file__).parent.parent / "data"))
DATA.mkdir(exist_ok=True)
DB_PATH = DATA / "gymbro.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  email TEXT NOT NULL, pw_hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS friendships(
  requester INTEGER NOT NULL REFERENCES users(id),
  addressee INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY(requester, addressee));
CREATE TABLE IF NOT EXISTS workouts(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  day TEXT NOT NULL, exercise TEXT NOT NULL, pr INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sets(
  id INTEGER PRIMARY KEY, workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  reps INTEGER NOT NULL, weight REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schedule(
  user_id INTEGER NOT NULL REFERENCES users(id), weekday INTEGER NOT NULL,
  PRIMARY KEY(user_id, weekday));
CREATE INDEX IF NOT EXISTS idx_workouts ON workouts(user_id, day);
"""


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        # Migration: databases created before PR tracking lack workouts.pr.
        # CREATE TABLE IF NOT EXISTS never alters an existing table, so add it by hand.
        if "pr" not in {r["name"] for r in c.execute("PRAGMA table_info(workouts)")}:
            c.execute("ALTER TABLE workouts ADD COLUMN pr INTEGER NOT NULL DEFAULT 0")


def secret_key() -> str:
    f = DATA / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


def hash_pw(pw: str) -> str:
    # Never store passwords. We store "salt$hash": a random salt makes identical
    # passwords hash differently, and PBKDF2's 200k iterations make brute force slow.
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    salt, h = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return hmac.compare_digest(h, test)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def stats(c, user_id: int, today: date | None = None) -> dict:
    """Streak, 7-day adherence and this-week volume for one user.

    Rules (the product's core idea):
    * Walk backwards from today, one day at a time.
    * A day with a workout adds 1 to the streak.
    * A rest day (not in the schedule) is skipped: it neither adds nor breaks.
    * A scheduled day with no workout breaks the streak, except today, because
      the day isn't over yet.
    * Adherence = scheduled days in the last 7 that had a workout / scheduled days.
    * Volume = sum(reps * kg) since Monday of this week.
    `today` is a parameter so tests can pin the date instead of depending on the clock.
    """
    today = today or date.today()
    user = c.execute("SELECT created FROM users WHERE id=?", (user_id,)).fetchone()
    created = date.fromisoformat(user["created"])
    sched = {r["weekday"] for r in c.execute("SELECT weekday FROM schedule WHERE user_id=?", (user_id,))}
    done = {r["day"] for r in c.execute("SELECT DISTINCT day FROM workouts WHERE user_id=?", (user_id,))}

    streak, d = 0, today
    while d >= created - timedelta(days=0) and (today - d).days < 400:
        iso = d.isoformat()
        if iso in done:
            streak += 1
        elif d.weekday() in sched and d != today:
            break
        d -= timedelta(days=1)

    due = hit = 0
    for i in range(7):
        d = today - timedelta(days=i)
        if d.weekday() in sched and d >= created:
            due += 1
            hit += d.isoformat() in done
    adherence = round(100 * hit / due) if due else 0

    ws = week_start(today).isoformat()
    vol = c.execute(
        "SELECT COALESCE(SUM(s.reps*s.weight),0) v FROM sets s JOIN workouts w ON w.id=s.workout_id "
        "WHERE w.user_id=? AND w.day>=?", (user_id, ws)).fetchone()["v"]
    return {"streak": streak, "adherence": adherence, "volume": vol, "volume_fmt": fmt_vol(vol)}


def fmt_vol(v: float) -> str:
    return f"{v/1000:.1f}k" if v >= 1000 else f"{v:.0f}"


def friend_ids(c, uid: int) -> list[int]:
    rows = c.execute(
        "SELECT requester, addressee FROM friendships WHERE status='accepted' AND (requester=? OR addressee=?)",
        (uid, uid)).fetchall()
    return [r["addressee"] if r["requester"] == uid else r["requester"] for r in rows]


# ---------- engagement features ----------
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MILESTONES = [3, 7, 14, 30, 60, 100, 200, 365]
# Epley formula: estimated one-rep max. Lets us compare 5x100kg with 3x105kg fairly.
E1RM = "s.weight*(1+s.reps/30.0)"


def e1rm(weight: float, reps: int) -> float:
    return weight * (1 + reps / 30) if weight > 0 else 0.0


def best_e1rm(c, uid: int, exercise: str, upto_day: str) -> float:
    """Best estimated 1RM for an exercise on or before a day (used to detect PRs)."""
    r = c.execute(
        f"SELECT MAX({E1RM}) m FROM sets s JOIN workouts w ON w.id=s.workout_id "
        "WHERE w.user_id=? AND w.exercise=? COLLATE NOCASE AND w.day<=? AND s.weight>0",
        (uid, exercise, upto_day)).fetchone()
    return r["m"] or 0.0


def week_strip(c, uid: int, today: date) -> list[dict]:
    """Mon..Sun state for the home screen: done / missed / today / upcoming / rest."""
    created = date.fromisoformat(c.execute("SELECT created FROM users WHERE id=?", (uid,)).fetchone()["created"])
    sched = {r["weekday"] for r in c.execute("SELECT weekday FROM schedule WHERE user_id=?", (uid,))}
    done = {r["day"] for r in c.execute("SELECT DISTINCT day FROM workouts WHERE user_id=?", (uid,))}
    out = []
    for i in range(7):
        d = week_start(today) + timedelta(days=i)
        if d.isoformat() in done:
            st = "done"
        elif i not in sched or d < created:
            st = "rest"
        elif d == today:
            st = "today"
        else:
            st = "upcoming" if d > today else "missed"
        out.append({"letter": "MTWTFSS"[i], "name": DAYS[i], "state": st, "is_today": d == today})
    return out


def volume_between(c, uid: int, start: date, end: date) -> float:
    """Total kg lifted for start <= day < end."""
    return c.execute(
        "SELECT COALESCE(SUM(s.reps*s.weight),0) v FROM sets s JOIN workouts w ON w.id=s.workout_id "
        "WHERE w.user_id=? AND w.day>=? AND w.day<?", (uid, start.isoformat(), end.isoformat())).fetchone()["v"]


def milestone(streak: int) -> dict:
    """Next streak goal and progress toward it (a visible goal keeps people coming back)."""
    prev = 0
    for m in MILESTONES:
        if streak < m:
            return {"target": m, "left": m - streak, "pct": round(100 * (streak - prev) / (m - prev))}
        prev = m
    return {"target": None, "left": 0, "pct": 100}


def when_label(day: str, today: date) -> str:
    n = (today - date.fromisoformat(day)).days
    return "Today" if n <= 0 else "Yesterday" if n == 1 else f"{n}d ago"


def feed(c, ids: list[int], viewer: int, today: date, limit: int = 10) -> list[dict]:
    """Recent workouts from you and your friends, newest first (last 14 days)."""
    marks = ",".join("?" * len(ids))          # only "?" characters are interpolated: safe
    rows = c.execute(
        "SELECT w.user_id, w.day, w.exercise, w.pr, u.username, COUNT(s.id) n, COALESCE(SUM(s.reps*s.weight),0) vol "
        f"FROM workouts w JOIN users u ON u.id=w.user_id LEFT JOIN sets s ON s.workout_id=w.id "
        f"WHERE w.user_id IN ({marks}) AND w.day>=? GROUP BY w.id ORDER BY w.day DESC, w.id DESC LIMIT ?",
        (*ids, (today - timedelta(days=14)).isoformat(), limit)).fetchall()
    return [{"who": "You" if r["user_id"] == viewer else r["username"], "exercise": r["exercise"],
             "sets": r["n"], "vol": fmt_vol(r["vol"]), "pr": bool(r["pr"]),
             "when": when_label(r["day"], today)} for r in rows]


def last_sessions(c, uid: int) -> dict:
    """Most recent sets per exercise, so the log screen can show 'last time'."""
    out, seen = {}, set()
    for r in c.execute("SELECT id, exercise, day FROM workouts WHERE user_id=? ORDER BY day DESC, id DESC LIMIT 300",
                       (uid,)).fetchall():
        if r["exercise"].lower() in seen:
            continue
        seen.add(r["exercise"].lower())
        sets = [[x["reps"], x["weight"]] for x in
                c.execute("SELECT reps, weight FROM sets WHERE workout_id=? ORDER BY id", (r["id"],))]
        out[r["exercise"]] = {"day": r["day"], "sets": sets}
    return out


def chart(series: list[tuple], w: int = 320, h: int = 160, pad: int = 22) -> dict | None:
    """Turn [(day, value)] into SVG coordinates. Doing the maths here keeps templates dumb."""
    series = series[-30:]
    if not series:
        return None
    vals = [v for _, v in series]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo = hi - 1
    pts = []
    for i, (day, v) in enumerate(series):
        x = pad + (w - 2 * pad) * (i / (len(series) - 1) if len(series) > 1 else 0.5)
        y = h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)
        pts.append({"x": round(x, 1), "y": round(y, 1), "v": round(v), "day": day})
    line = " ".join(f"{p['x']},{p['y']}" for p in pts)
    area = f"{pts[0]['x']},{h - pad} {line} {pts[-1]['x']},{h - pad}"
    return {"w": w, "h": h, "pts": pts, "line": line, "area": area, "lo": round(min(vals)), "hi": round(hi)}
