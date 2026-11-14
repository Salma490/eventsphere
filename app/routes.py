from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import bus, models
from app.auth import get_current_user, require_role, verify_csrf
from app.db import get_db
from app.web import render, flash

router = APIRouter()
EVENT_CATEGORIES = ["Concert", "Workshop", "Seminar", "Conference", "Other"]

def go(url: str):
    return RedirectResponse(url, status_code=303)

def need_login(request: Request):
    flash(request, "Please log in first.", "warning")
    return go("/login")

def parse_dt(value: str):
    return datetime.fromisoformat(value)

# =====================================================================
#  ATTENDEE PAGES
# =====================================================================
@router.get("/")
def home(request: Request, q: str = "", category: str = "",
         db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = db.query(models.Event).filter(models.Event.status == "published",
                                          models.Event.end_time >= datetime.now())
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Event.title.ilike(like), models.Event.description.ilike(like)))
    if category:
        query = query.filter(models.Event.category == category)
    events = query.order_by(models.Event.start_time).all()
    venues = {v.id: v for v in db.query(models.Venue).all()}
    left = dict(db.query(models.TicketCategory.event_id, func.sum(models.TicketCategory.remaining))
                .group_by(models.TicketCategory.event_id).all())
    prices = dict(db.query(models.TicketCategory.event_id, func.min(models.TicketCategory.price))
                  .group_by(models.TicketCategory.event_id).all())
    categories = [r[0] for r in db.query(models.Event.category)
                  .filter(models.Event.status == "published").distinct().all()]
    return render(request, "home.html", user, events=events, venues=venues, left=left,
                  prices=prices, categories=categories, q=q, category=category)

@router.get("/events/{event_id}")
def event_page(event_id: int, request: Request,
               db: Session = Depends(get_db), user=Depends(get_current_user)):
    event = db.get(models.Event, event_id)
    if event is None or (event.status == "draft" and not (user and user.role == "admin")):
        raise HTTPException(status_code=404, detail="Event not found")
    venue = db.get(models.Venue, event.venue_id)
    cats = db.query(models.TicketCategory).filter_by(event_id=event.id).all()
    my_wait = []
    if user:
        my_wait = db.query(models.WaitlistEntry).filter(
            models.WaitlistEntry.user_id == user.id, models.WaitlistEntry.event_id == event.id,
            models.WaitlistEntry.status.in_(["waiting", "held"])).all()
    can_book = event.status == "published" and event.start_time > datetime.now()
    return render(request, "event.html", user, event=event, venue=venue, cats=cats,
                  my_wait=my_wait, can_book=can_book)

@router.post("/events/{event_id}/book")
def book_tickets(event_id: int, request: Request,
                 category_id: int = Form(...), quantity: int = Form(...),
                 db: Session = Depends(get_db), user=Depends(get_current_user),
                 _=Depends(verify_csrf)):
    if not user:
        return need_login(request)
    reply = bus.send(db, "Web", "BookingAgent", "REQUEST",
                     {"action": "book", "user_id": user.id, "event_id": event_id,
                      "category_id": category_id, "quantity": quantity})
    c = reply["content"]
    if reply["performative"] == "AGREE":
        flash(request, f"Booking confirmed! Simulated payment reference {c['payment_ref']}, "
                       f"total LKR {c['amount']:.2f}.")
        return go("/my/bookings")
    flash(request, c.get("reason", "Booking failed."), "danger")
    return go(f"/events/{event_id}")

@router.post("/events/{event_id}/waitlist")
def join_waitlist(event_id: int, request: Request,
                  category_id: int = Form(...), quantity: int = Form(...),
                  db: Session = Depends(get_db), user=Depends(get_current_user),
                  _=Depends(verify_csrf)):
    if not user:
        return need_login(request)
    reply = bus.send(db, "Web", "WaitlistAgent", "REQUEST",
                     {"action": "join", "user_id": user.id, "event_id": event_id,
                      "category_id": category_id, "quantity": quantity})
    if reply["performative"] == "AGREE":
        flash(request, f"You joined the waitlist (position {reply['content']['position']}).")
    else:
        flash(request, reply["content"].get("reason", "Could not join the waitlist."), "danger")
    return go(f"/events/{event_id}")

@router.post("/waitlist/{wid}/claim")
def claim_hold(wid: int, request: Request,
               db: Session = Depends(get_db), user=Depends(get_current_user),
               _=Depends(verify_csrf)):
    if not user:
        return need_login(request)
    reply = bus.send(db, "Web", "WaitlistAgent", "REQUEST",
                     {"action": "claim", "waitlist_id": wid, "user_id": user.id})
    if reply["performative"] == "AGREE":
        flash(request, f"Seats claimed! Payment reference {reply['content']['payment_ref']}.")
    else:
        flash(request, reply["content"].get("reason", "Could not claim."), "danger")
    return go("/my/bookings")

@router.get("/my/bookings")
def my_bookings(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return need_login(request)
    now = datetime.now()
    rows = (db.query(models.Booking, models.Event, models.TicketCategory)
            .join(models.Event, models.Booking.event_id == models.Event.id)
            .join(models.TicketCategory, models.Booking.category_id == models.TicketCategory.id)
            .filter(models.Booking.user_id == user.id)
            .order_by(models.Booking.id.desc()).all())
    waits = (db.query(models.WaitlistEntry, models.Event, models.TicketCategory)
             .join(models.Event, models.WaitlistEntry.event_id == models.Event.id)
             .join(models.TicketCategory, models.WaitlistEntry.category_id == models.TicketCategory.id)
             .filter(models.WaitlistEntry.user_id == user.id,
                     models.WaitlistEntry.status.in_(["waiting", "held"]))
             .order_by(models.WaitlistEntry.id.desc()).all())
    remaining = {w.id: max(0, int((w.hold_expires_at - now).total_seconds()))
                 for w, _, _ in waits if w.status == "held" and w.hold_expires_at}
    return render(request, "my_bookings.html", user, rows=rows, waits=waits,
                  remaining=remaining, now=now)

@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, request: Request,
                   db: Session = Depends(get_db), user=Depends(get_current_user),
                   _=Depends(verify_csrf)):
    if not user:
        return need_login(request)
    reply = bus.send(db, "Web", "BookingAgent", "REQUEST",
                     {"action": "cancel_booking", "booking_id": booking_id, "user_id": user.id})
    if reply["performative"] == "AGREE":
        flash(request, f"Booking cancelled. Refund: LKR {reply['content']['refund']:.2f} "
                       f"({reply['content']['percent']}%).")
    else:
        flash(request, reply["content"].get("reason", "Could not cancel."), "danger")
    return go("/my/bookings")

@router.get("/my/notifications")
def my_notifications(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return need_login(request)
    items = (db.query(models.Notification).filter_by(user_id=user.id)
             .order_by(models.Notification.id.desc()).limit(50).all())
    return render(request, "notifications.html", user, items=items)

# ---------------------------- private venue hire ----------------------------
@router.get("/hire")
def hire_page(request: Request, venue_id: int = 0, start: str = "", end: str = "",
              guests: int = 0, purpose: str = "",
              db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return need_login(request)
    venues = db.query(models.Venue).filter_by(open_for_hire=True).order_by(models.Venue.name).all()
    mine = (db.query(models.HireRequest).filter_by(user_id=user.id)
            .order_by(models.HireRequest.id.desc()).all())
    names = {v.id: v.name for v in db.query(models.Venue).all()}
    return render(request, "hire.html", user, venues=venues, mine=mine, names=names,
                  f={"venue_id": venue_id, "start": start, "end": end,
                     "guests": guests or "", "purpose": purpose})

@router.post("/hire")
def hire_submit(request: Request,
                venue_id: int = Form(...), start: str = Form(...), end: str = Form(...),
                purpose: str = Form(...), guests: int = Form(...),
                db: Session = Depends(get_db), user=Depends(get_current_user),
                _=Depends(verify_csrf)):
    if not user:
        return need_login(request)
    venue = db.get(models.Venue, venue_id)
    try:
        s, e = parse_dt(start), parse_dt(end)
    except ValueError:
        flash(request, "Please enter valid start and end times.", "danger")
        return go("/hire")
    if venue is None or guests < 1:
        flash(request, "Choose a venue and enter the number of guests.", "danger")
        return go("/hire")
    req = models.HireRequest(user_id=user.id, venue_id=venue.id, start_time=s, end_time=e,
                             purpose=purpose.strip()[:200], guests=guests)
    db.add(req)
    db.commit()
    reply = bus.send(db, "Web", "VenueAgent", "REQUEST",
                     {"action": "hire_request", "request_id": req.id})
    db.refresh(req)
    return render(request, "hire_result.html", user, reply=reply, req=req, venue=venue)

# =====================================================================
#  ADMIN PAGES
# =====================================================================
@router.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db),
                    user=Depends(require_role("admin"))):
    stats = []
    for ev in db.query(models.Event).order_by(models.Event.start_time.desc()).all():
        capacity = db.query(func.coalesce(func.sum(models.TicketCategory.quantity), 0)).filter(
            models.TicketCategory.event_id == ev.id).scalar()
        sold = db.query(func.coalesce(func.sum(models.Booking.quantity), 0)).filter(
            models.Booking.event_id == ev.id, models.Booking.status == "confirmed").scalar()
        revenue = db.query(func.coalesce(func.sum(models.Booking.amount - models.Booking.refund_amount), 0)).filter(
            models.Booking.event_id == ev.id).scalar()
        checked = db.query(func.count(models.Ticket.id)).filter(
            models.Ticket.event_id == ev.id, models.Ticket.status == "used").scalar()
        stats.append({"event": ev, "capacity": capacity, "sold": sold,
                      "revenue": revenue, "checked_in": checked})
    pending = db.query(models.HireRequest).filter_by(status="pending").count()
    return render(request, "admin.html", user, stats=stats, pending=pending)

# ----------------------------- venues -----------------------------
@router.get("/admin/venues")
def venues_page(request: Request, db: Session = Depends(get_db), user=Depends(require_role("admin"))):
    venues = db.query(models.Venue).order_by(models.Venue.name).all()
    return render(request, "venues.html", user, venues=venues)

@router.get("/admin/venues/new")
def venue_new(request: Request, user=Depends(require_role("admin"))):
    return render(request, "venue_form.html", user, venue=None)

@router.post("/admin/venues/new")
def venue_create(request: Request,
                 name: str = Form(...), location: str = Form(...), capacity: int = Form(...),
                 description: str = Form(""), image_url: str = Form(""),
                 hourly_rate: float = Form(0), open_for_hire: str = Form(""),
                 db: Session = Depends(get_db), user=Depends(require_role("admin")),
                 _=Depends(verify_csrf)):
    if capacity < 1 or hourly_rate < 0:
        flash(request, "Capacity must be at least 1 and the rate cannot be negative.", "danger")
        return go("/admin/venues/new")
    db.add(models.Venue(name=name.strip(), location=location.strip(), capacity=capacity,
                        description=description.strip(), image_url=image_url.strip(),
                        hourly_rate=hourly_rate, open_for_hire=bool(open_for_hire)))
    db.commit()
    flash(request, "Venue created.")
    return go("/admin/venues")

@router.get("/admin/venues/{venue_id}/edit")
def venue_edit(venue_id: int, request: Request, db: Session = Depends(get_db),
               user=Depends(require_role("admin"))):
    venue = db.get(models.Venue, venue_id)
    if venue is None:
        raise HTTPException(status_code=404, detail="Venue not found")
    return render(request, "venue_form.html", user, venue=venue)

@router.post("/admin/venues/{venue_id}/edit")
def venue_update(venue_id: int, request: Request,
                 name: str = Form(...), location: str = Form(...), capacity: int = Form(...),
                 description: str = Form(""), image_url: str = Form(""),
                 hourly_rate: float = Form(0), open_for_hire: str = Form(""),
                 db: Session = Depends(get_db), user=Depends(require_role("admin")),
                 _=Depends(verify_csrf)):
    venue = db.get(models.Venue, venue_id)
    if venue is None:
        raise HTTPException(status_code=404, detail="Venue not found")
    if capacity < 1 or hourly_rate < 0:
        flash(request, "Capacity must be at least 1 and the rate cannot be negative.", "danger")
        return go(f"/admin/venues/{venue_id}/edit")
    venue.name, venue.location, venue.capacity = name.strip(), location.strip(), capacity
    venue.description, venue.image_url = description.strip(), image_url.strip()
    venue.hourly_rate, venue.open_for_hire = hourly_rate, bool(open_for_hire)
    db.commit()
    flash(request, "Venue updated.")
    return go("/admin/venues")

# ----------------------------- events -----------------------------
@router.get("/admin/events")
def events_admin(request: Request, db: Session = Depends(get_db), user=Depends(require_role("admin"))):
    events = db.query(models.Event).order_by(models.Event.start_time.desc()).all()
    venues = {v.id: v for v in db.query(models.Venue).all()}
    return render(request, "events_admin.html", user, events=events, venues=venues)

@router.get("/admin/events/new")
def event_new(request: Request, db: Session = Depends(get_db), user=Depends(require_role("admin"))):
    venues = db.query(models.Venue).order_by(models.Venue.name).all()
    return render(request, "event_form.html", user, venues=venues, error=None, f={}, rows=[],
                  categories=EVENT_CATEGORIES)

@router.post("/admin/events/new")
def event_create(request: Request,
                 title: str = Form(...), category: str = Form(...), venue_id: int = Form(...),
                 start: str = Form(...), end: str = Form(...), description: str = Form(""),
                 action: str = Form("draft"),
                 cat_name: list[str] = Form(default=[]),
                 cat_price: list[str] = Form(default=[]),
                 cat_qty: list[str] = Form(default=[]),
                 db: Session = Depends(get_db), user=Depends(require_role("admin")),
                 _=Depends(verify_csrf)):
    venues = db.query(models.Venue).order_by(models.Venue.name).all()
    f = {"title": title, "category": category, "venue_id": venue_id, "start": start,
         "end": end, "description": description}
    rows = [{"name": n, "price": p, "qty": q} for n, p, q in zip(cat_name, cat_price, cat_qty)]

    def fail(message):
        return render(request, "event_form.html", user, venues=venues, error=message, f=f,
                      rows=rows, categories=EVENT_CATEGORIES)

    try:
        s, e = parse_dt(start), parse_dt(end)
    except ValueError:
        return fail("Please enter valid start and end times.")
    if e <= s:
        return fail("The end time must be after the start time.")

    cats = []
    for n, p, q in zip(cat_name, cat_price, cat_qty):
        if not n.strip():
            continue
        try:
            price, qty = float(p), int(q)
        except ValueError:
            return fail(f"Ticket category '{n}' needs a numeric price and quantity.")
        if price < 0 or qty < 1:
            return fail(f"Ticket category '{n}' needs a price of 0 or more and a quantity of at least 1.")
        cats.append((n.strip(), price, qty))
    if not cats:
        return fail("Add at least one ticket category.")

    venue = db.get(models.Venue, venue_id)
    if venue is None:
        return fail("Choose a venue.")
    total = sum(q for _, _, q in cats)
    if total > venue.capacity:
        return fail(f"Total tickets ({total}) exceed the venue capacity ({venue.capacity}).")

    reply = bus.send(db, "Web", "VenueAgent", "REQUEST",
                     {"action": "check_slot", "venue_id": venue.id,
                      "start": s.isoformat(), "end": e.isoformat()})
    if reply["performative"] != "AGREE":
        return fail("VenueAgent refused this time slot: " + reply["content"]["reason"])

    event = models.Event(title=title.strip(), category=category, venue_id=venue.id,
                         start_time=s, end_time=e, description=description.strip(),
                         status="published" if action == "publish" else "draft")
    db.add(event)
    db.commit()
    for n, price, qty in cats:
        db.add(models.TicketCategory(event_id=event.id, name=n, price=price,
                                     quantity=qty, remaining=qty))
    db.commit()
    flash(request, f"Event '{event.title}' saved as {event.status}.")
    return go("/admin/events")

@router.post("/admin/events/{event_id}/publish")
def event_publish(event_id: int, request: Request, db: Session = Depends(get_db),
                  user=Depends(require_role("admin")), _=Depends(verify_csrf)):
    ev = db.get(models.Event, event_id)
    if ev is None or ev.status != "draft":
        flash(request, "Only draft events can be published.", "danger")
        return go("/admin/events")
    reply = bus.send(db, "Web", "VenueAgent", "REQUEST",
                     {"action": "check_slot", "venue_id": ev.venue_id, "exclude_event_id": ev.id,
                      "start": ev.start_time.isoformat(), "end": ev.end_time.isoformat()})
    if reply["performative"] != "AGREE":
        flash(request, "Cannot publish: " + reply["content"]["reason"], "danger")
        return go("/admin/events")
    ev.status = "published"
    db.commit()
    flash(request, f"'{ev.title}' is now published.")
    return go("/admin/events")

@router.post("/admin/events/{event_id}/cancel")
def event_cancel(event_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(require_role("admin")), _=Depends(verify_csrf)):
    ev = db.get(models.Event, event_id)
    if ev is None or ev.status != "published":
        flash(request, "Only published events can be cancelled.", "danger")
        return go("/admin/events")
    reply = bus.send(db, "Web", "BookingAgent", "REQUEST",
                     {"action": "cancel_event", "event_id": ev.id})
    c = reply["content"]
    flash(request, f"Event cancelled. {c['refunded_bookings']} booking(s) fully refunded "
                   f"(LKR {c['total_refunded']:.2f}).")
    return go("/admin/events")

# ----------------------------- hire requests -----------------------------
@router.get("/admin/hires")
def hires_admin(request: Request, db: Session = Depends(get_db), user=Depends(require_role("admin"))):
    rows = (db.query(models.HireRequest, models.User, models.Venue)
            .join(models.User, models.HireRequest.user_id == models.User.id)
            .join(models.Venue, models.HireRequest.venue_id == models.Venue.id)
            .order_by(models.HireRequest.id.desc()).all())
    return render(request, "hires_admin.html", user, rows=rows)

@router.post("/admin/hires/{request_id}/decide")
def hire_decide(request_id: int, request: Request, decision: str = Form(...),
                db: Session = Depends(get_db), user=Depends(require_role("admin")),
                _=Depends(verify_csrf)):
    if decision not in ("approve", "reject"):
        flash(request, "Invalid decision.", "danger")
        return go("/admin/hires")
    reply = bus.send(db, "Web", "VenueAgent", "REQUEST",
                     {"action": "hire_decision", "request_id": request_id, "decision": decision})
    if reply["performative"] == "AGREE":
        flash(request, f"Request {reply['content']['status']}.")
    else:
        flash(request, reply["content"]["reason"], "danger")
    return go("/admin/hires")