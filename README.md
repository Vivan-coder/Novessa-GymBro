# Novessa-GymBro
Stay consistent with your workouts and compete with your friends for the best Gym Bro
A Novessa product.

> Status: in development. Live demo coming soon.

<!-- Add screenshot here: ![Dashboard](docs/screenshots/dashboard.png) -->
<img width="1917" height="962" alt="image" src="https://github.com/user-attachments/assets/6cb69d49-6778-4cc6-b81d-82600e94b32e" />
<img width="1917" height="966" alt="image" src="https://github.com/user-attachments/assets/8fb97fd1-02ad-4612-92c9-ca4d84de7bab" />


## What it does

Log your workouts, set your own gym schedule, and see how you stack up against friends on consistency and progress, not just raw weight lifted.

- **Workout logging:** exercises, sets, reps, and weight
- **Custom schedule:** choose your gym days; your streak only breaks if you miss a scheduled day
- **Friends:** send and accept requests; only accepted friends see your stats
- **Leaderboard:** weekly training volume, streak, and adherence % side by side

## Roadmap

- [x] Project structure and repository
- [ ] Database models
- [ ] Register and login
- [ ] Workout logging
- [ ] Schedule and streak logic
- [ ] Friends (request and accept)
- [ ] Leaderboard
- [ ] Tests
- [ ] Deployment

## Tech stack

- **Backend:** Python, FastAPI, SQLModel
- **Database:** SQLite (development)
- **Frontend:** Jinja templates, HTML, CSS, JavaScript (mobile-first)
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

Then open http://127.0.0.1:8000.

## Design decisions

- **Streaks follow your schedule.** Rest days don't break a streak, and schedule changes only affect future days, so history is never rewritten.
- **Streaks are calculated, not stored.** Computing them from workout dates keeps them accurate if data changes.
- **Consent for sharing.** Friendships need an accepted request before any stats are visible.
- **Consistency over raw lifts.** Adherence % makes comparisons fair across experience levels.

## What's next

To be filled in as the project grows.

## Author

Built by Vivan, Computer Science student.
