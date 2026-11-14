from datetime import datetime, timedelta
from app import bus
from app.agents.base import BaseAgent
from app.models import Venue, Event, HireRequest

BUFFER = timedelta(minutes=30)   # turnaround time between activities
WEEKEND_SURCHARGE = 0.25         # +25% on Saturday/Sunday
AUTO_MAX_HOURS = 4               # longer hires are escalated to admin
AUTO_MAX_FILL = 0.8              # more than 80% of capacity is escalated
MAX_HOURS = 12

def _dt(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value

def _overlaps(s1, e1, s2, e2):
    return s1 < e2 + BUFFER and s2 < e1 + BUFFER

class VenueAgent(BaseAgent):
    """Goal: keep every venue calendar free of clashes.
    Rules:
      1. Event slot is refused if it overlaps (including a 30 min buffer) another
         non-cancelled event or a confirmed hire at the same venue.
      2. Hire is refused if venue not open for hire, guests > capacity, duration
         is <= 0 or > 12 hours, or start is in the past.
      3. Hire on a clash -> PROPOSE up to 2 other times (same day) and 2 other venues.
      4. Hire is auto-confirmed if duration <= 4h and guests <= 80% of capacity,
         otherwise escalated to the admin (status pending).
      5. Price = hours x hourly rate, +25% if the hire starts on Saturday or Sunday.
    """
    name = "VenueAgent"

    # ---------- helpers (the agent's own private functions) ----------
    def find_conflict(self, db, venue_id, start, end, exclude_event_id=None):
        for ev in db.query(Event).filter(Event.venue_id == venue_id, Event.status != "cancelled").all():
            if exclude_event_id and ev.id == exclude_event_id:
                continue
            if _overlaps(start, end, ev.start_time, ev.end_time):
                return f"event '{ev.title}' ({ev.start_time:%d %b %H:%M}-{ev.end_time:%H:%M})"
        for h in db.query(HireRequest).filter(HireRequest.venue_id == venue_id,
                                              HireRequest.status == "confirmed").all():
            if _overlaps(start, end, h.start_time, h.end_time):
                return f"a private hire ({h.start_time:%d %b %H:%M}-{h.end_time:%H:%M})"
        return None

    def price(self, venue, start, end):
        hours = (end - start).total_seconds() / 3600
        total = hours * venue.hourly_rate
        if start.weekday() >= 5:
            total *= 1 + WEEKEND_SURCHARGE
        return round(total, 2)

    def alternatives(self, db, req, venue, same_venue=True):
        found = []
        duration = req.end_time - req.start_time
        if same_venue:
            day = req.start_time.replace(hour=0, minute=0, second=0, microsecond=0)
            options = []
            for h in range(8, 21):
                s = day + timedelta(hours=h)
                if s == req.start_time or s < datetime.now() or s + duration > day + timedelta(hours=23):
                    continue
                options.append(s)
            options.sort(key=lambda s: abs((s - req.start_time).total_seconds()))
            for s in options:
                if self.find_conflict(db, venue.id, s, s + duration) is None:
                    found.append({"venue_id": venue.id, "venue_name": venue.name,
                                  "start": s.isoformat(), "end": (s + duration).isoformat(),
                                  "price": self.price(venue, s, s + duration),
                                  "reason": "Same venue, free at this time"})
                    if len(found) == 2:
                        break
        others = 0
        for v in db.query(Venue).filter(Venue.open_for_hire == True, Venue.id != venue.id,  # noqa: E712
                                        Venue.capacity >= req.guests).all():
            if self.find_conflict(db, v.id, req.start_time, req.end_time) is None:
                found.append({"venue_id": v.id, "venue_name": v.name,
                              "start": req.start_time.isoformat(), "end": req.end_time.isoformat(),
                              "price": self.price(v, req.start_time, req.end_time),
                              "reason": "Different venue, free at your requested time"})
                others += 1
                if others == 2:
                    break
        return found

    # ---------- messages this agent understands ----------
    def on_check_slot(self, db, msg):
        c = msg.content
        start, end = _dt(c["start"]), _dt(c["end"])
        if end <= start:
            return ("REFUSE", {"reason": "End time must be after start time."})
        if db.get(Venue, c["venue_id"]) is None:
            return ("REFUSE", {"reason": "Venue does not exist."})
        conflict = self.find_conflict(db, c["venue_id"], start, end, c.get("exclude_event_id"))
        if conflict:
            return ("REFUSE", {"reason": f"Time clash with {conflict} (30 min turnaround buffer applies)."})
        return ("AGREE", {"message": "Slot is free."})

    def on_hire_request(self, db, msg):
        req = db.get(HireRequest, msg.content["request_id"])
        if req is None:
            return ("FAILURE", {"reason": "Hire request not found."})
        venue = db.get(Venue, req.venue_id)
        hours = (req.end_time - req.start_time).total_seconds() / 3600

        def reject(reason):
            req.status = "rejected"
            db.commit()
            return ("REFUSE", {"reason": reason})

        if not venue.open_for_hire:
            return reject("This venue is not open for private hire.")
        if hours <= 0 or hours > MAX_HOURS:
            return reject(f"Hire must be between 0 and {MAX_HOURS} hours.")
        if req.start_time < datetime.now():
            return reject("The start time is in the past.")
        if req.guests > venue.capacity:
            alts = self.alternatives(db, req, venue, same_venue=False)
            req.status = "rejected"
            db.commit()
            reason = f"{req.guests} guests exceeds the capacity of {venue.capacity}."
            if alts:
                return ("PROPOSE", {"reason": reason, "alternatives": alts})
            return ("REFUSE", {"reason": reason})

        conflict = self.find_conflict(db, venue.id, req.start_time, req.end_time)
        if conflict:
            alts = self.alternatives(db, req, venue)
            req.status = "rejected"
            db.commit()
            reason = f"Venue is not free: clash with {conflict}."
            if alts:
                return ("PROPOSE", {"reason": reason, "alternatives": alts})
            return ("REFUSE", {"reason": reason + " No alternatives found."})

        req.price = self.price(venue, req.start_time, req.end_time)
        if hours <= AUTO_MAX_HOURS and req.guests <= venue.capacity * AUTO_MAX_FILL:
            req.status = "confirmed"
            db.commit()
            bus.send(db, self.name, "NotificationAgent", "INFORM", {"event": "notify", "user_id": req.user_id, "text": f"Your hire of {venue.name} is confirmed. Price: LKR {req.price:.2f}."}, msg.conversation_id)
            return ("AGREE", {"decision": "confirmed", "price": req.price})

        req.status = "pending"
        db.commit()
        bus.send(db, self.name, "NotificationAgent", "INFORM", {"event": "notify_admins", "text": f"Hire request #{req.id} for {venue.name} needs your approval."}, msg.conversation_id)
        return ("INFORM", {"decision": "escalated", "price": req.price, "reason": "Long or large hires need admin approval."})

    def on_hire_decision(self, db, msg):
        c = msg.content  # {"request_id", "decision": "approve" | "reject"}
        req = db.get(HireRequest, c["request_id"])
        if req is None or req.status != "pending":
            return ("REFUSE", {"reason": "Only pending requests can be decided."})
        if c["decision"] == "approve":
            conflict = self.find_conflict(db, req.venue_id, req.start_time, req.end_time)
            if conflict:
                return ("REFUSE", {"reason": f"Cannot approve: clash with {conflict}."})
            req.status = "confirmed"
            text = "Your venue hire request was approved."
        else:
            req.status = "rejected"
            text = "Your venue hire request was rejected by the admin."
        db.commit()
        bus.send(db, self.name, "NotificationAgent", "INFORM", {"event": "notify", "user_id": req.user_id, "text": text}, msg.conversation_id)
        return ("AGREE", {"status": req.status})