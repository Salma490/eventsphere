import os
os.environ["DATABASE_URL"] = "sqlite:///./test_eventsphere.db"  # separate test database

import pytest
from datetime import datetime, timedelta
from app.db import Base, engine, SessionLocal
from app import models
from app.agents import register_all

@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    register_all()
    yield

@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()

@pytest.fixture
def world(db):
    admin = models.User(name="Admin", email="a@x.lk", password_hash="x", role="admin")
    u1 = models.User(name="User One", email="u1@x.lk", password_hash="x", role="attendee")
    u2 = models.User(name="User Two", email="u2@x.lk", password_hash="x", role="attendee")
    venue = models.Venue(name="Main Hall", location="Campus", capacity=100,
                         hourly_rate=1000, open_for_hire=True)
    db.add_all([admin, u1, u2, venue])
    db.commit()
    start = (datetime.now() + timedelta(days=10)).replace(hour=14, minute=0, second=0, microsecond=0)
    event = models.Event(title="Concert", category="Music", venue_id=venue.id,
                         start_time=start, end_time=start + timedelta(hours=3), status="published")
    db.add(event)
    db.commit()
    cat = models.TicketCategory(event_id=event.id, name="General", price=500, quantity=2, remaining=2)
    db.add(cat)
    db.commit()
    return {"admin": admin, "u1": u1, "u2": u2, "venue": venue, "event": event, "cat": cat}