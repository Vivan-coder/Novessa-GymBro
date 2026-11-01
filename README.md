# Novessa-GymBro
Stay consistent with your workouts and compete with your friends for the best Gym Bro
A Novessa product.

> Status: working MVP. Live demo coming soon.

<!-- Add screenshot here: ![Dashboard](docs/screenshots/dashboard.png) -->

## What it does

Log your workouts, set your own gym schedule, and see how you stack up against friends on consistency and progress, not just raw weight lifted.

- **Workout logging:** exercises, sets, reps, and weight, with your last session shown as a hint
- **Custom schedule:** choose your gym days; your streak only breaks if you miss a scheduled day
- **Friends:** send and accept requests; only accepted friends see your stats
- **Crew leaderboard:** weekly training volume, streak, and adherence % side by side, plus a friends' activity feed
- **PRs and progress:** automatic personal-record detection and a strength chart per lift

## Roadmap

- [x] Project structure and repository
- [x] Database models
- [x] Register and login
- [x] Workout logging
- [x] Schedule and streak logic
- [x] Friends (request and accept)
- [x] Leaderboard
- [x] Tests
- [ ] Edit a logged workout
- [ ] Deployment

## Tech stack

- **Backend:** Python, FastAPI
- **Database:** SQLite via the standard-library `sqlite3` module (development)
- **Frontend:** Jinja templates, HTML, CSS, a little JavaScript (mobile-first)
- **Testing:** pytest

## Run locally

```
git clone https://github.com/Vivan-coder/Novessa-GymBro.git
cd Novessa-GymBro
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000. The SQLite database is created in `data/` on first start (set `GYMBRO_DATA` to use another folder). Run the tests with `python -m pytest`.

## Features (and why each one exists)

Modelled on what Strong, Hevy and Strava do well. Each feature targets one reason people quit gym apps.

| Feature | Why |
|---|---|
| Streak hero + milestone bar (3, 7, 14, 30... days) | A visible next goal beats a bare number |
| Week strip (done / missed / rest) | See the week in one glance |
| "Last time" hint + "Use these sets" when logging | Progressive overload needs knowing what you did last |
| PR detection (Epley estimated 1RM) | Celebrates real progress: `weight × (1 + reps/30)` compares 5×100 with 3×105 fairly |
| Progress charts per lift (server-built SVG, no JS library) | Shows the strength curve going up |
| Rest timer | Replaces a second app on your phone |
| Crew: leaderboard (streak or volume) + activity feed | Social accountability, the main retention driver |

**Removed as useless:** the email field (nothing used it), separate Friends and Leaderboard pages (same data twice, now one Crew tab), and Schedule as a nav tab (set once, now a link from Home). The old URLs redirect.

**Schema migration:** adding PR tracking meant a new `workouts.pr` column. `CREATE TABLE IF NOT EXISTS` never alters an existing table, so `db.init()` checks `PRAGMA table_info` and runs `ALTER TABLE` for old databases. Existing data is kept (there is a test for this).

## Design decisions

- **Streaks follow your schedule.** Rest days don't break a streak, and schedule changes only affect future days, so history is never rewritten.
- **Streaks are calculated, not stored.** Computing them from workout dates keeps them accurate if data changes.
- **Consent for sharing.** Friendships need an accepted request before any stats are visible.
- **Consistency over raw lifts.** Adherence % makes comparisons fair across experience levels.

## How it fits together

```
Browser ──HTTP──> app/main.py (routes) ──> app/db.py (SQL + rules) ──> data/gymbro.db
                        │
                        └──> app/templates/*.html (Jinja2) ──> HTML back to the browser
```

| File | Job | Why it's separate |
|---|---|---|
| `app/main.py` | URL routes, validation, sessions | Thin web layer |
| `app/db.py` | Schema, password hashing, streak/PR/chart maths | Testable without HTTP |
| `app/templates/` | HTML pages, all extending `base.html` | One layout, no duplication |
| `app/static/style.css` | Styling | Cached by the browser |
| `tests/test_app.py` | End-to-end and unit tests | Safety net for changes |

### Database (5 tables)

- `users`: username (unique, case-insensitive), password hash. (The legacy `email` column is kept but no longer collected.)
- `workouts`: one row per exercise per day, with a `pr` flag; `sets`: reps and kg, many per workout.
- `schedule`: which weekdays (0 = Mon) a user plans to train.
- `friendships`: `requester → addressee` with `status` pending/accepted.

Volume, streak and adherence are **computed from workouts on demand**, not stored. Stored counters can drift out of sync with the data. Computed values are always correct. The trade-off is query cost, which is trivial at this scale.

### Key concepts you can learn from this code

1. **Sessions.** After login the server puts `uid` in a signed cookie (Starlette `SessionMiddleware`). The cookie is tamper-proof, not encrypted, so never put secrets in it. The signing key is generated once and stored in `data/secret.key`.
2. **Password storage.** Passwords are hashed with a random salt and PBKDF2 (`db.hash_pw`), compared in constant time (`hmac.compare_digest`).
3. **Post/Redirect/Get.** Every form POST ends in a 303 redirect, so pressing refresh doesn't resubmit.
4. **Security basics.** Parameterised SQL (`?`) blocks SQL injection. Jinja2 auto-escaping blocks XSS. All routes except `/login` and `/register` require a session (`current_user`). An `Origin` check plus `SameSite=Lax` cookies protect against CSRF.
5. **Streak rules** are documented in `db.stats` and pinned by a unit test with a fixed date. Taking `today` as a parameter makes time-dependent code testable.
6. **Progressive enhancement.** Forms work without JavaScript. The scripts on the log page only add the "Add set" button, the custom-exercise box, "last time" and the rest timer.

## Known limits and next steps (good exercises)

- **CSRF** is handled by an `Origin` header check in middleware plus `SameSite=Lax` cookies. Per-form tokens are the more traditional approach, so try adding them as an exercise.
- **Login rate limit** (5 failures per 5 minutes per IP and username) lives in memory, so it resets on restart and isn't shared across processes. Use Redis if you scale out.
- Set `https_only=True` on the session middleware when deployed behind HTTPS.
- You can delete a logged workout (own workouts only) but not edit one yet.
- The crew board ranks by streak, then adherence, then volume (or by volume with `?by=volume`). Change the sort keys in `crew()` if you prefer another rule.
- `requirements.txt` still lists `sqlmodel`/`SQLAlchemy` from the original plan; the app currently uses plain `sqlite3`. Adopt SQLModel or drop them.
- For several users or machines, move from SQLite to PostgreSQL and add migrations (Alembic).

## Author

Built by Vivan, Computer Science student.
