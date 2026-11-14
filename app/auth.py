import secrets
from fastapi import Request, HTTPException, Depends
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token

async def verify_csrf(request: Request):
    form = await request.form()
    if not form.get("csrf_token") or form.get("csrf_token") != request.session.get("csrf"):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)

def require_role(*roles):
    def checker(user=Depends(get_current_user)):
        if user is None:
            raise HTTPException(status_code=401, detail="Please log in first")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission")
        return user
    return checker