"""End-to-end tests: drive the real app through HTTP, the way a browser would.

Why test this way? Our logic lives in route handlers + SQL, so testing through
HTTP exercises everything (forms, sessions, DB, templates) at once. Each test
gets a fresh temp database so tests never affect each other or real data.
"""
import importlib
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    # db.py reads GYMBRO_DATA at import time, so set it first, then reload.
    monkeypatch.setenv("GYMBRO_DATA", str(tmp_path))
    from app import db, main
    importlib.reload(db)
    importlib.reload(main)
    return main.app


def signup(client, name):
    return client.post("/register", data={"username": name, "email": f"{name}@x.com", "password": "secret1"})


def test_pages_require_login(app):
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_register_login_and_rules(app):
    c = TestClient(app)
    assert signup(c, "alice").status_code == 200
    assert "taken" in signup(TestClient(app), "ALICE").text          # usernames are case-insensitive
    assert "Wrong username" in TestClient(app).post("/login", data={"username": "alice", "password": "nope"}).text
    assert "at least 6" in TestClient(app).post(
        "/register", data={"username": "carol", "email": "c@x.com", "password": "123"}).text


def test_logging_computes_volume_and_validates(app):
    c = TestClient(app)
    signup(c, "alice")
    r = c.post("/log", data={"day": date.today().isoformat(), "exercise": "__custom__", "custom": "Dips",
                             "reps": ["10", "8", ""], "weight": ["50", "60", ""]})
    assert "Saved Dips: 2 sets" in r.text
    assert "980" in r.text                                            # 10*50 + 8*60
    assert "future" in c.post("/log", data={"day": "2999-01-01", "exercise": "Squat",
                                            "reps": ["5"], "weight": ["5"]}).text
    assert "at least one set" in c.post("/log", data={"day": date.today().isoformat(), "exercise": "Squat",
                                                      "reps": [""], "weight": [""]}).text


def test_schedule_and_streak(app):
    from app import db
    c = TestClient(app)
    signup(c, "alice")
    c.post("/schedule", data={"day": [str(d) for d in range(7)]})     # every day is a gym day
    c.post("/log", data={"day": date.today().isoformat(), "exercise": "Squat", "reps": ["5"], "weight": ["100"]})
    with db.conn() as conn:
        s = db.stats(conn, 1)
    assert s["streak"] == 1 and s["adherence"] == 100


def test_streak_ignores_rest_days_but_breaks_on_missed_gym_day():
    """Pure logic test on stats(): no HTTP needed."""
    import sqlite3
    from datetime import timedelta
    from app import db
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    today = date(2026, 11, 4)                                          # a Wednesday
    c.execute("INSERT INTO users VALUES(1,'a','a@x','h',?)", ((today - timedelta(days=30)).isoformat(),))
    c.executemany("INSERT INTO schedule VALUES(1,?)", [(0,), (2,), (4,)])   # Mon, Wed, Fri
    for d in (today, today - timedelta(days=2)):                       # Wed + Mon done
        c.execute("INSERT INTO workouts(user_id,day,exercise) VALUES(1,?,'x')", (d.isoformat(),))
    # Fri before Mon was missed -> streak counts Wed + Mon only; Tue/Sun rest days don't break it.
    assert db.stats(c, 1, today)["streak"] == 2


def test_friends_flow_and_leaderboard(app):
    a, b = TestClient(app), TestClient(app)
    signup(a, "alice"); signup(b, "bob")
    assert "Request sent" in a.post("/friends/add", data={"username": "bob"}).text
    assert "alice" in b.get("/crew").text and "Accept" in b.get("/crew").text
    b.post("/friends/accept", data={"user_id": 1})
    board = a.get("/crew").text
    assert "bob" in board and "You" in board
    assert "No user" in a.post("/friends/add", data={"username": "ghost"}).text
    assert a.get("/leaderboard", follow_redirects=False).headers["location"] == "/crew"   # old URL still works


def test_pr_detected_only_when_beating_previous_best(app):
    c = TestClient(app)
    signup(c, "alice")
    day = date.today().isoformat()
    first = c.post("/log", data={"day": day, "exercise": "Squat", "reps": ["5"], "weight": ["100"]})
    assert "New PR" not in first.text                                   # first log is a baseline
    assert "New PR on Squat" in c.post("/log", data={"day": day, "exercise": "Squat", "reps": ["5"], "weight": ["105"]}).text
    assert "New PR" not in c.post("/log", data={"day": day, "exercise": "squat", "reps": ["5"], "weight": ["90"]}).text


def test_progress_page_and_empty_state(app):
    c = TestClient(app)
    signup(c, "alice")
    assert "Nothing to chart yet" in c.get("/progress").text
    c.post("/log", data={"day": date.today().isoformat(), "exercise": "Squat", "reps": ["5"], "weight": ["100"]})
    page = c.get("/progress").text
    assert "<svg class=\"chart\"" in page and "117" in page              # 100*(1+5/30) = 116.7


def test_old_database_is_migrated(tmp_path, monkeypatch):
    """A DB made before the 'pr' column existed must be upgraded, not crash."""
    import sqlite3
    monkeypatch.setenv("GYMBRO_DATA", str(tmp_path))
    old = sqlite3.connect(tmp_path / "gymbro.db")
    old.execute("CREATE TABLE workouts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, day TEXT NOT NULL, exercise TEXT NOT NULL)")
    old.commit(); old.close()
    from app import db
    importlib.reload(db)
    db.init()
    with db.conn() as c:
        assert "pr" in {r["name"] for r in c.execute("PRAGMA table_info(workouts)")}


def test_delete_workout_only_own(app):
    a, b = TestClient(app), TestClient(app)
    signup(a, "alice"); signup(b, "bob")
    a.post("/log", data={"day": date.today().isoformat(), "exercise": "Squat", "reps": ["5"], "weight": ["100"]})
    assert "not found" in b.post("/log/1/delete").text            # bob can't delete alice's workout
    assert "Workout deleted" in a.post("/log/1/delete").text


def test_cross_site_post_blocked(app):
    c = TestClient(app)
    r = c.post("/login", data={"username": "x", "password": "y"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_login_rate_limit(app):
    c = TestClient(app)
    signup(c, "alice")
    bad = TestClient(app)
    for _ in range(5):
        assert "Wrong" in bad.post("/login", data={"username": "alice", "password": "no"}).text
    assert "Too many" in bad.post("/login", data={"username": "alice", "password": "secret1"}).text
