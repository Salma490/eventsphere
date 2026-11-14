from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.auth import get_csrf_token

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def flash(request: Request, text: str, kind: str = "success"):
    request.session["flash"] = request.session.get("flash", []) + [[kind, text]]

def render(request: Request, name: str, user=None, **ctx):
    flashes = request.session.pop("flash", [])
    context = {"user": user, "csrf_token": get_csrf_token(request), "flashes": flashes, **ctx}
    return templates.TemplateResponse(request, name, context)