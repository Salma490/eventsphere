from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app import bus, models
from app.auth import hash_password, verify_password

def book(db, w, user, qty, cid=None):
    return bus.send(db, "Web", "BookingAgent", "REQUEST",
                    {"action": "book", "user_id": user.id, "event_id": w["event"].id,
                     "category_id": w["cat"].id, "quantity": qty}, cid)

def cancel(db, booking_id, user):
    return bus.send(db, "Web", "BookingAgent", "REQUEST",
                    {"action": "cancel_booking", "booking_id": booking_id, "user_id": user.id}, None)

def checkin(db, code, event_id):
    return bus.send(db, "Web", "TicketAgent", "REQUEST",
                    {"action": "check_in", "code": code, "event_id": event_id}, None)

def first_code(db):
    return db.query(models.Ticket).first().code

def make_hire(db, w, start, hours, guests):
    req = models.HireRequest(user_id=w["u1"].id, venue_id=w["venue"].id, start_time=start,
                             end_time=start + timedelta(hours=hours), purpose="Farewell", guests=guests)
    db.add(req)
    db.commit()
    return req

def ask_hire(db, req):
    return bus.send(db, "Web", "VenueAgent", "REQUEST",
                    {"action": "hire_request", "request_id": req.id}, None)

# ---------------- BookingAgent ----------------
def test_overbooking_is_refused(db, world):
    r1 = book(db, world, world["u1"], 2)
    assert r1["performative"] == "AGREE"
    r2 = book(db, world, world["u2"], 1)
    assert r2["performative"] == "REFUSE"
    assert r2["content"]["can_waitlist"] is True
    db.refresh(world["cat"])
    assert world["cat"].remaining == 0

def test_more_than_six_tickets_refused(db, world):
    r = book(db, world, world["u1"], 7)
    assert r["performative"] == "REFUSE"

def test_full_refund_when_cancelled_early(db, world):
    r = book(db, world, world["u1"], 1)
    c = cancel(db, r["content"]["booking_id"], world["u1"])
    assert c["performative"] == "AGREE"
    assert c["content"]["refund"] == 500.0

def test_half_refund_when_cancelled_within_a_week(db, world):
    world["event"].start_time = datetime.now() + timedelta(days=2)
    world["event"].end_time = datetime.now() + timedelta(days=2, hours=3)
    db.commit()
    r = book(db, world, world["u1"], 1)
    c = cancel(db, r["content"]["booking_id"], world["u1"])
    assert c["content"]["refund"] == 250.0

# ---------------- TicketAgent ----------------
def test_ticket_codes_are_unique_and_long(db, world):
    book(db, world, world["u1"], 2)
    codes = [t.code for t in db.query(models.Ticket).all()]
    assert len(codes) == 2 and len(set(codes)) == 2
    assert all(len(c) >= 20 for c in codes)

def test_double_check_in_is_rejected(db, world):
    book(db, world, world["u1"], 1)
    world["event"].start_time = datetime.now() + timedelta(hours=1)
    world["event"].end_time = datetime.now() + timedelta(hours=3)
    db.commit()
    code = first_code(db)
    first = checkin(db, code, world["event"].id)
    assert first["performative"] == "AGREE" and first["content"]["result"] == "ADMIT"
    second = checkin(db, code, world["event"].id)
    assert second["performative"] == "REFUSE"
    assert second["content"]["result"] == "ALREADY_USED"

def test_ticket_for_another_event_is_rejected(db, world):
    book(db, world, world["u1"], 1)
    other = models.Event(title="Seminar", category="Talk", venue_id=world["venue"].id,
                         start_time=datetime.now() + timedelta(days=20),
                         end_time=datetime.now() + timedelta(days=20, hours=2), status="published")
    db.add(other)
    db.commit()
    r = checkin(db, first_code(db), other.id)
    assert r["content"]["result"] == "WRONG_EVENT"

def test_cancelled_ticket_is_rejected(db, world):
    r = book(db, world, world["u1"], 1)
    cancel(db, r["content"]["booking_id"], world["u1"])
    res = checkin(db, first_code(db), world["event"].id)
    assert res["content"]["result"] == "CANCELLED"

# ---------------- VenueAgent ----------------
def test_venue_refuses_clash_and_buffer(db, world):
    ev = world["event"]

    def slot(start):
        return bus.send(db, "Web", "VenueAgent", "REQUEST",
                        {"action": "check_slot", "venue_id": world["venue"].id,
                         "start": start.isoformat(),
                         "end": (start + timedelta(hours=1)).isoformat()}, None)

    assert slot(ev.start_time + timedelta(hours=1))["performative"] == "REFUSE"      # overlap
    r = slot(ev.end_time + timedelta(minutes=10))                                    # inside buffer
    assert r["performative"] == "REFUSE" and "clash" in r["content"]["reason"].lower()
    assert slot(ev.end_time + timedelta(hours=1))["performative"] == "AGREE"         # free

def test_hire_is_auto_confirmed(db, world):
    start = (datetime.now() + timedelta(days=20)).replace(hour=10, minute=0, second=0, microsecond=0)
    req = make_hire(db, world, start, 2, 20)
    r = ask_hire(db, req)
    assert r["performative"] == "AGREE" and r["content"]["decision"] == "confirmed"
    db.refresh(req)
    assert req.status == "confirmed" and req.price > 0

def test_hire_clash_gets_alternatives(db, world):
    req = make_hire(db, world, world["event"].start_time, 2, 20)
    r = ask_hire(db, req)
    assert r["performative"] == "PROPOSE"
    assert r["content"]["reason"]
    assert len(r["content"]["alternatives"]) > 0
    assert all(a["reason"] for a in r["content"]["alternatives"])

def test_large_hire_is_escalated_to_admin(db, world):
    start = (datetime.now() + timedelta(days=20)).replace(hour=10, minute=0, second=0, microsecond=0)
    req = make_hire(db, world, start, 2, 90)   # 90 of 100 = over 80%
    r = ask_hire(db, req)
    assert r["performative"] == "INFORM" and r["content"]["decision"] == "escalated"
    db.refresh(req)
    assert req.status == "pending"

# ---------------- WaitlistAgent ----------------
def fill_and_wait(db, w):
    r1 = book(db, w, w["u1"], 2)
    join = bus.send(db, "Web", "WaitlistAgent", "REQUEST",
                    {"action": "join", "user_id": w["u2"].id, "event_id": w["event"].id,
                     "category_id": w["cat"].id, "quantity": 1}, None)
    assert join["performative"] == "AGREE"
    cancel(db, r1["content"]["booking_id"], w["u1"])   # frees 2 seats -> hold for u2
    return db.query(models.WaitlistEntry).first()

def test_waitlist_hold_expires_and_seats_return(db, world):
    entry = fill_and_wait(db, world)
    db.refresh(entry)
    assert entry.status == "held"
    entry.hold_expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    bus.AGENTS["WaitlistAgent"].tick(db)     # the timer-driven sweep
    db.refresh(entry)
    db.refresh(world["cat"])
    assert entry.status == "expired"
    assert world["cat"].remaining == 2

def test_waitlist_claim_books_the_held_seat(db, world):
    entry = fill_and_wait(db, world)
    r = bus.send(db, "Web", "WaitlistAgent", "REQUEST",
                 {"action": "claim", "waitlist_id": entry.id, "user_id": world["u2"].id}, None)
    assert r["performative"] == "AGREE"
    db.refresh(entry)
    assert entry.status == "booked"
    assert db.query(models.Booking).filter_by(user_id=world["u2"].id).count() == 1

# ---------------- NotificationAgent + traceability ----------------
def test_notification_agent_stores_notification(db, world):
    bus.send(db, "Web", "NotificationAgent", "INFORM",
             {"event": "notify", "user_id": world["u1"].id, "text": "Hello"}, None)
    assert db.query(models.Notification).count() == 1

def test_one_conversation_is_traceable_across_agents(db, world):
    book(db, world, world["u1"], 1, cid="c-trace")
    rows = db.query(models.Message).filter_by(conversation_id="c-trace").all()
    names = {r.sender for r in rows} | {r.receiver for r in rows}
    assert len(rows) >= 5
    assert {"Web", "BookingAgent", "TicketAgent", "NotificationAgent"} <= names

# ---------------- Security ----------------
def test_password_is_hashed():
    h = hash_password("secret123")
    assert h != "secret123" and verify_password("secret123", h)

def test_admin_page_needs_login():
    from app.main import app
    client = TestClient(app)
    assert client.get("/admin").status_code == 401