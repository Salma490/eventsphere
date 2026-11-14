import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app import models, bus
from app.agents import register_all
from app.auth import (hash_password, verify_password, get_csrf_token,
                      verify_csrf, get_current_user, require_role)
from app.db import Base, engine, get_db, SessionLocal

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def seed_users():
    """Create the three demo accounts if they do not exist yet."""
    db = SessionLocal()
    try:
        demo = [
            ("Admin User", "admin@eventsphere.lk", "Admin@123", "admin"),
            ("Gate Staff", "staff@eventsphere.lk", "Staff@123", "staff"),
            ("Demo Attendee", "attendee@eventsphere.lk", "Attendee@123", "attendee"),
        ]
        for name, email, password, role in demo:
            if not db.query(models.User).filter_by(email=email).first():
                db.add(models.User(name=name, email=email,
                                   password_hash=hash_password(password), role=role))
        db.commit()
    finally:
        db.close()

def sweep_once():
    db = SessionLocal()
    try:
        bus.AGENTS["WaitlistAgent"].tick(db)
    finally:
        db.close()

async def sweep_loop():
    """Proactive behaviour: the WaitlistAgent wakes up every 30 seconds by itself."""
    while True:
        try:
            await asyncio.to_thread(sweep_once)
        except Exception as exc:
            print("Waitlist sweep error:", exc)
        await asyncio.sleep(30)

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_users()
    register_all()
    task = asyncio.create_task(sweep_loop())
    yield
    task.cancel()

app = FastAPI(title="EventSphere", lifespan=lifespan)
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"))

def render(request: Request, name: str, user=None, **ctx):
    context = {"user": user, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context)

@app.get("/")
def home(request: Request, user=Depends(get_current_user)):
    return render(request, "home.html", user)

@app.get("/register")
def register_page(request: Request, user=Depends(get_current_user)):
    return render(request, "register.html", user)

@app.post("/register")
def register_submit(request: Request,
                    name: str = Form(...), email: str = Form(...),
                    password: str = Form(...),
                    db: Session = Depends(get_db),
                    _=Depends(verify_csrf)):
    email = email.strip().lower()
    if len(password) < 6:
        return render(request, "register.html", None, error="Password must be at least 6 characters.")
    if db.query(models.User).filter_by(email=email).first():
        return render(request, "register.html", None, error="That email is already registered.")
    user = models.User(name=name.strip(), email=email,
                       password_hash=hash_password(password), role="attendee")
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)

@app.get("/login")
def login_page(request: Request, user=Depends(get_current_user)):
    return render(request, "login.html", user)

@app.post("/login")
def login_submit(request: Request,
                 email: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db),
                 _=Depends(verify_csrf)):
    user = db.query(models.User).filter_by(email=email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", None, error="Wrong email or password.")
    request.session["user_id"] = user.id
    if user.role == "admin":
        return RedirectResponse("/admin", status_code=303)
    if user.role == "staff":
        return RedirectResponse("/scanner", status_code=303)
    return RedirectResponse("/", status_code=303)

@app.post("/logout")
def logout(request: Request, _=Depends(verify_csrf)):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.get("/admin")
def admin_page(request: Request, user=Depends(require_role("admin"))):
    return render(request, "admin.html", user)

@app.get("/admin/messages")
def message_log(request: Request, db: Session = Depends(get_db),
                user=Depends(require_role("admin"))):
    msgs = db.query(models.Message).order_by(models.Message.id.desc()).limit(300).all()
    return render(request, "messages.html", user, messages=msgs)

@app.get("/scanner")
def scanner_page(request: Request, user=Depends(require_role("staff", "admin"))):
    return render(request, "scanner.html", user)