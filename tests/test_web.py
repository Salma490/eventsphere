from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app import bus, models
from app.main import app

def get_token(client, url):
    html = client.get(url).text
    return html.split('name="csrf_token" value="')[1].split('"')[0]

def login(client, email, password):
    token = get_token(client, "/login")
    return client.post("/login", data={"email": email, "password": password, "csrf_token": token},
                       follow_redirects=False)

def book(db, user_id, w, qty=1):
    return bus.send(db, "Web", "BookingAgent", "REQUEST",
                    {"action": "book", "user_id": user_id, "event_id": w["event"].id,
                     "category_id": w["cat"].id, "quantity": qty})

def test_attendee_cannot_use_scanner(db, world):
    with TestClient(app) as client:
        login(client, "attendee@eventsphere.lk", "Attendee@123")
        assert client.get("/scanner").status_code == 403

def test_scanner_route_admits_a_ticket_only_once(db, world):
    attendee = None
    with TestClient(app) as client:
        attendee = db.query(models.User).filter_by(email="attendee@eventsphere.lk").first()
        assert book(db, attendee.id, world)["performative"] == "AGREE"
        world["event"].start_time = datetime.now() + timedelta(hours=1)
        world["event"].end_time = datetime.now() + timedelta(hours=3)
        db.commit()
        code = db.query(models.Ticket).first().code

        login(client, "staff@eventsphere.lk", "Staff@123")
        token = get_token(client, "/scanner")
        data = {"code": code, "event_id": world["event"].id, "csrf_token": token}
        first = client.post("/scanner/check", data=data).json()
        second = client.post("/scanner/check", data=data).json()
    assert first["admitted"] is True and first["result"] == "ADMIT"
    assert second["admitted"] is False and second["result"] == "ALREADY_USED"

def test_qr_image_is_a_png_and_needs_login(db, world):
    with TestClient(app) as client:
        attendee = db.query(models.User).filter_by(email="attendee@eventsphere.lk").first()
        book(db, attendee.id, world)
        code = db.query(models.Ticket).first().code
        assert client.get(f"/qr/{code}").status_code == 401
        login(client, "attendee@eventsphere.lk", "Attendee@123")
        r = client.get(f"/qr/{code}")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")