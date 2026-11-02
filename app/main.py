"""Web layer: routes only. Each handler (1) checks who you are, (2) validates
input, (3) talks to SQLite via db.py, (4) renders a template or redirects.

Pattern used for every form: POST -> redirect (303) -> GET. This is the
"Post/Redirect/Get" pattern; it stops a browser refresh from re-submitting a
form. Success/error messages ride along in the query string (?msg= / ?err=).
SQL always uses ? placeholders, never string formatting, to prevent SQL injection.
Jinja2 auto-escapes output, which prevents XSS from usernames/exercise names.
"""
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db

BASE = Path(__file__).parent
db.init()
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=db.secret_key(), same_site="lax",
                   max_age=60 * 60 * 24 * 30, https_only=False)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

DEFAULT_EXERCISES = ["Bench press", "Squat", "Deadlift", "Overhead press", "Barbell row", "Pull-up"]


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """CSRF defence: browsers always send an Origin header on cross-site form
    POSTs. If it names a different host than the one we're serving, someone's
    other website is trying to submit a form as you, so reject it.
    (Non-browser clients send no Origin; they can't ride your cookies anyway.)"""
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


# Login rate limit: 5 failures per (ip, username) per 5 minutes. In memory, so
# it resets on restart and isn't shared between processes; fine for one server.
FAILS: dict[tuple, list[float]] = {}
MAX_FAILS, WINDOW = 5, 300


def too_many_fails(key) -> bool:
    now = time.time()
    FAILS[key] = [t for t in FAILS.get(key, []) if now - t < WINDOW]
    return len(FAILS[key]) >= MAX_FAILS


class NeedLogin(Exception):
    pass


@app.exception_handler(NeedLogin)
def _need_login(request, exc):
    return RedirectResponse("/login", 303)


def current_user(request: Request):
    uid = request.session.get("uid")
    if uid:
        with db.conn() as c:
            u = c.execute("SELECT id, username FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            return u
    raise NeedLogin()


def show(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, msg: str = "", err: bool = False, pr: bool = False):
    if msg:
        path += ("?err=" if err else "?msg=") + quote(msg) + ("&pr=1" if pr else "")
    return RedirectResponse(path, 303)


def flash(request: Request):
    q = request.query_params
    return {"msg": q.get("msg"), "err": q.get("err"), "pr": q.get("pr")}


# ---------- auth ----------
@app.get("/login")
def login_page(request: Request):
    return show(request, "login.html", **flash(request))


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    key = (request.client.host if request.client else "?", username.strip().lower())
    if too_many_fails(key):
        return back("/login", "Too many attempts. Try again in a few minutes.", True)
    with db.conn() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not u or not db.check_pw(password, u["pw_hash"]):
        FAILS.setdefault(key, []).append(time.time())
        return back("/login", "Wrong username or password.", True)
    FAILS.pop(key, None)
    request.session["uid"] = u["id"]
    return back("/")


@app.post("/register")
def register(request: Request, username: str = Form(""), password: str = Form("")):
    # No email: nothing in the app ever used it, so we don't collect it.
    username = username.strip()
    if not (3 <= len(username) <= 24) or not username.replace("_", "").isalnum():
        return back("/login", "Username must be 3-24 letters, numbers or underscores.", True)
    if len(password) < 6:
        return back("/login", "Password must be at least 6 characters.", True)
    try:
        with db.conn() as c:
            cur = c.execute("INSERT INTO users(username,email,pw_hash,created) VALUES(?,?,?,?)",
                            (username, "", db.hash_pw(password), date.today().isoformat()))
            uid = cur.lastrowid
            c.executemany("INSERT INTO schedule VALUES(?,?)", [(uid, d) for d in (0, 2, 4)])
    except Exception:
        return back("/login", "That username is taken.", True)
    request.session["uid"] = uid
    return back("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return back("/login")


# ---------- home ----------
@app.get("/")
def home(request: Request):
    u = current_user(request)
    today = date.today()
    with db.conn() as c:
        s = db.stats(c, u["id"], today)
        strip = db.week_strip(c, u["id"], today)
        last_week = db.volume_between(c, u["id"], db.week_start(today) - timedelta(days=7), db.week_start(today))
        prs = c.execute("SELECT COUNT(*) n FROM workouts WHERE user_id=? AND pr=1 AND day>=?",
                        (u["id"], (today - timedelta(days=30)).isoformat())).fetchone()["n"]
        items = db.feed(c, [u["id"], *db.friend_ids(c, u["id"])], u["id"], today, 5)
    delta = round(100 * (s["volume"] - last_week) / last_week) if last_week else None
    return show(request, "dashboard.html", user=u, s=s, strip=strip, ms=db.milestone(s["streak"]),
                today_state=strip[today.weekday()]["state"], delta=delta, prs=prs, feed=items, **flash(request))


# ---------- log ----------
@app.get("/log")
def log_page(request: Request):
    u = current_user(request)
    with db.conn() as c:
        last = db.last_sessions(c, u["id"])
        recent = c.execute(
            "SELECT w.id, w.day, w.exercise, w.pr, COUNT(s.id) n, COALESCE(SUM(s.reps*s.weight),0) vol FROM workouts w "
            "LEFT JOIN sets s ON s.workout_id=w.id WHERE w.user_id=? GROUP BY w.id ORDER BY w.day DESC, w.id DESC LIMIT 8",
            (u["id"],)).fetchall()
    known = {n.lower() for n in DEFAULT_EXERCISES}
    exercises = DEFAULT_EXERCISES + [n for n in last if n.lower() not in known]
    return show(request, "log.html", exercises=exercises, recent=recent, last=last,
                today=date.today().isoformat(), today_label=date.today().strftime("%a %d %b"), fmt=db.fmt_vol, **flash(request))


@app.post("/log")
async def log_save(request: Request):
    u = current_user(request)
    f = await request.form()
    # The date is automatic: today, unless a valid past date is explicitly sent.
    try:
        day = date.fromisoformat(f["day"]) if f.get("day") else date.today()
    except ValueError:
        return back("/log", "That date isn't valid.", True)
    if day > date.today():
        return back("/log", "You can't log a future workout.", True)
    exercise = (f.get("custom") or "") if f.get("exercise") == "__custom__" else f.get("exercise", "")
    exercise = " ".join(exercise.split())[:60]                 # trim and collapse spaces
    exercise = exercise[:1].upper() + exercise[1:]
    if not exercise:
        return back("/log", "Choose or name an exercise.", True)
    sets = []
    for reps, kg in zip(f.getlist("reps"), f.getlist("weight")):
        if not reps.strip():
            if kg.strip():
                return back("/log", "A set has a weight but no reps.", True)
            continue
        try:
            r, w = int(reps), float(kg or 0)
        except ValueError:
            return back("/log", "Reps and weight must be numbers.", True)
        if not (0 < r <= 1000) or not (0 <= w <= 2000):
            return back("/log", "Those numbers look off.", True)
        sets.append((r, w))
    if not sets:
        return back("/log", "Add at least one set with reps.", True)
    new_best = max(db.e1rm(w, r) for r, w in sets)
    with db.conn() as c:
        # A PR = beating your previous best estimated 1RM on this lift. The very
        # first time you log a lift is a baseline, not a PR.
        prev = db.best_e1rm(c, u["id"], exercise, day.isoformat())
        is_pr = prev > 0 and new_best > prev + 1e-9
        wid = c.execute("INSERT INTO workouts(user_id,day,exercise,pr) VALUES(?,?,?,?)",
                        (u["id"], day.isoformat(), exercise, int(is_pr))).lastrowid
        c.executemany("INSERT INTO sets(workout_id,reps,weight) VALUES(?,?,?)", [(wid, r, w) for r, w in sets])
    if is_pr:
        return back("/", f"New PR on {exercise}! Est. 1RM {new_best:.0f} kg (+{new_best - prev:.0f}). Saved {len(sets)} sets.",
                    pr=True)
    return back("/", f"Saved {exercise}: {len(sets)} sets.")


@app.post("/log/{workout_id}/delete")
def log_delete(request: Request, workout_id: int):
    u = current_user(request)
    with db.conn() as c:
        # user_id in the WHERE clause is the authorisation check: you can only
        # delete your own rows. Its sets go too (ON DELETE CASCADE).
        n = c.execute("DELETE FROM workouts WHERE id=? AND user_id=?", (workout_id, u["id"])).rowcount
    return back("/log", "Workout deleted." if n else "Workout not found.", not n)


# ---------- progress ----------
@app.get("/progress")
def progress(request: Request, ex: str = ""):
    u = current_user(request)
    with db.conn() as c:
        lifts = c.execute(
            f"SELECT w.exercise, COUNT(DISTINCT w.id) sessions, MAX({db.E1RM}) best FROM workouts w "
            "LEFT JOIN sets s ON s.workout_id=w.id WHERE w.user_id=? GROUP BY w.exercise COLLATE NOCASE "
            "ORDER BY MAX(w.id) DESC", (u["id"],)).fetchall()
        totals = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(pr),0) prs FROM workouts WHERE user_id=?", (u["id"],)).fetchone()
        volume = c.execute(
            "SELECT COALESCE(SUM(s.reps*s.weight),0) v FROM sets s JOIN workouts w ON w.id=s.workout_id "
            "WHERE w.user_id=?", (u["id"],)).fetchone()["v"]
        names = [l["exercise"] for l in lifts]
        sel = next((n for n in names if n.lower() == ex.lower()), names[0] if names else None)
        series, picked = [], None
        if sel:
            series = [(r["day"], r["m"]) for r in c.execute(
                f"SELECT w.day, MAX({db.E1RM}) m FROM workouts w JOIN sets s ON s.workout_id=w.id "
                "WHERE w.user_id=? AND w.exercise=? COLLATE NOCASE AND s.weight>0 GROUP BY w.day ORDER BY w.day",
                (u["id"], sel))]
            picked = next(l for l in lifts if l["exercise"] == sel)
    return show(request, "progress.html", lifts=lifts, sel=sel, picked=picked, chart=db.chart(series),
                totals=totals, volume=db.fmt_vol(volume), **flash(request))


# ---------- schedule ----------
@app.get("/schedule")
def schedule_page(request: Request):
    u = current_user(request)
    with db.conn() as c:
        sched = {r["weekday"] for r in c.execute("SELECT weekday FROM schedule WHERE user_id=?", (u["id"],))}
    return show(request, "schedule.html", sched=sched, **flash(request))


@app.post("/schedule")
async def schedule_save(request: Request):
    u = current_user(request)
    f = await request.form()
    days = {int(d) for d in f.getlist("day") if d.isdigit() and 0 <= int(d) <= 6}
    if not days:
        return back("/schedule", "Pick at least one gym day.", True)
    with db.conn() as c:
        c.execute("DELETE FROM schedule WHERE user_id=?", (u["id"],))
        c.executemany("INSERT INTO schedule VALUES(?,?)", [(u["id"], d) for d in days])
    return back("/schedule", "Schedule saved.")


# ---------- crew (friends + leaderboard + activity, merged into one page) ----------
@app.get("/crew")
def crew(request: Request, by: str = "streak"):
    u = current_user(request)
    by = by if by in ("streak", "volume") else "streak"
    today = date.today()
    with db.conn() as c:
        ids = [u["id"], *db.friend_ids(c, u["id"])]
        rows = []
        for uid in ids:
            name = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()["username"]
            s = db.stats(c, uid, today)
            rows.append({"name": name, "me": uid == u["id"], **s})
        reqs = c.execute(
            "SELECT u.id, u.username FROM friendships f JOIN users u ON u.id=f.requester "
            "WHERE f.addressee=? AND f.status='pending'", (u["id"],)).fetchall()
        items = db.feed(c, ids, u["id"], today, 12)
    key = (lambda r: (r["volume"], r["streak"])) if by == "volume" else (lambda r: (r["streak"], r["adherence"], r["volume"]))
    rows.sort(key=key, reverse=True)
    return show(request, "crew.html", rows=rows, requests=reqs, feed=items, by=by, **flash(request))


@app.post("/friends/add")
def friend_add(request: Request, username: str = Form("")):
    u = current_user(request)
    with db.conn() as c:
        t = c.execute("SELECT id, username FROM users WHERE username=?", (username.strip(),)).fetchone()
        if not t:
            return back("/crew", "No user with that name.", True)
        if t["id"] == u["id"]:
            return back("/crew", "That's you.", True)
        ex = c.execute("SELECT * FROM friendships WHERE (requester=? AND addressee=?) OR (requester=? AND addressee=?)",
                       (u["id"], t["id"], t["id"], u["id"])).fetchone()
        if ex:
            if ex["status"] == "pending" and ex["addressee"] == u["id"]:
                c.execute("UPDATE friendships SET status='accepted' WHERE requester=? AND addressee=?",
                          (t["id"], u["id"]))
                return back("/crew", f"You and {t['username']} are now friends.")
            return back("/crew", "Already friends or request pending.", True)
        c.execute("INSERT INTO friendships(requester,addressee) VALUES(?,?)", (u["id"], t["id"]))
    return back("/crew", f"Request sent to {t['username']}.")


@app.post("/friends/accept")
def friend_accept(request: Request, user_id: int = Form(...)):
    u = current_user(request)
    with db.conn() as c:
        c.execute("UPDATE friendships SET status='accepted' WHERE requester=? AND addressee=? AND status='pending'",
                  (user_id, u["id"]))
    return back("/crew", "Friend added.")


# Old URLs from the first version keep working.
@app.get("/friends")
@app.get("/leaderboard")
def _old_urls():
    return RedirectResponse("/crew", 303)
