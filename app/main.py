# Preview server for the UI shell. Replace the fake data with database queries later.
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).parent
app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

LEADERS = [
    {"name": "Aditya", "streak": 9, "adherence": 100, "volume": "15.2k"},
    {"name": "You", "streak": 5, "adherence": 86, "volume": "12.4k"},
    {"name": "Koshika", "streak": 4, "adherence": 75, "volume": "9.8k"},
    {"name": "Vishit", "streak": 2, "adherence": 60, "volume": "11.1k"},
]

def show(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)

@app.get("/")
def home(request: Request): return show(request, "dashboard.html")

@app.get("/login")
def login(request: Request): return show(request, "login.html")

@app.get("/log")
def log(request: Request): return show(request, "log.html")

@app.get("/schedule")
def schedule(request: Request): return show(request, "schedule.html")

@app.get("/friends")
def friends(request: Request):
    return show(request, "friends.html", requests=["Maya", "Rohan"], friends=LEADERS[:3])

@app.get("/leaderboard")
def leaderboard(request: Request): return show(request, "leaderboard.html", rows=LEADERS)
