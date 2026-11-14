import secrets
from datetime import datetime, timedelta
from app import bus
from app.agents.base import BaseAgent
from app.models import Booking, Ticket, Event

CHECKIN_OPENS_BEFORE = timedelta(hours=2)
SUSPICIOUS_SCANS = 3

class TicketAgent(BaseAgent):
    """Goal: admit each ticket exactly once.
    Rules (check-in decisions, in this order):
      1. Code not found -> UNKNOWN_CODE
      2. Ticket cancelled -> CANCELLED
      3. Ticket belongs to a different event -> WRONG_EVENT
      4. Ticket already used -> ALREADY_USED (3+ scans of one code alerts the admins)
      5. Earlier than 2 hours before start, or after the event ended -> OUTSIDE_CHECKIN_TIME
      6. Otherwise -> ADMIT (ticket becomes 'used')
    State: scan count per code, total tickets issued.
    """
    name = "TicketAgent"

    def on_issue_tickets(self, db, msg):
        booking = db.get(Booking, msg.content["booking_id"])
        if booking is None:
            return ("FAILURE", {"reason": "Booking not found."})
        for _ in range(booking.quantity):
            db.add(Ticket(booking_id=booking.id, event_id=booking.event_id,
                          code=secrets.token_urlsafe(16)))
        db.commit()
        self.incr_state(db, "tickets_issued", booking.quantity)
        return ("AGREE", {"issued": booking.quantity})

    def on_cancel_tickets(self, db, msg):
        tickets = db.query(Ticket).filter_by(booking_id=msg.content["booking_id"], status="valid").all()
        for t in tickets:
            t.status = "cancelled"
        db.commit()
        return ("AGREE", {"cancelled": len(tickets)})

    def on_check_in(self, db, msg):
        c = msg.content  # {"code", "event_id"}
        code = str(c.get("code", "")).strip()
        ticket = db.query(Ticket).filter_by(code=code).first()
        if ticket is None:
            self.incr_state(db, "unknown_code_scans")
            return ("REFUSE", {"result": "UNKNOWN_CODE", "reason": "This code does not match any ticket."})

        scans = self.incr_state(db, f"scans:{code}")
        event = db.get(Event, ticket.event_id)

        if ticket.status == "cancelled":
            return ("REFUSE", {"result": "CANCELLED", "reason": "This ticket was cancelled."})
        if ticket.event_id != int(c["event_id"]):
            return ("REFUSE", {"result": "WRONG_EVENT",
                               "reason": f"This ticket is for a different event: {event.title}."})
        if ticket.status == "used":
            if scans >= SUSPICIOUS_SCANS:
                bus.send(db, self.name, "NotificationAgent", "INFORM",
                         {"event": "notify_admins",
                          "text": f"Suspicious: ticket for '{event.title}' scanned {scans} times (shared screenshot?)."},
                         msg.conversation_id)
            return ("REFUSE", {"result": "ALREADY_USED",
                               "reason": f"Already used at {ticket.used_at:%H:%M} (scan #{scans})."})

        now = datetime.now()
        if now < event.start_time - CHECKIN_OPENS_BEFORE:
            return ("REFUSE", {"result": "OUTSIDE_CHECKIN_TIME",
                               "reason": "Check-in opens 2 hours before the event starts."})
        if now > event.end_time:
            return ("REFUSE", {"result": "OUTSIDE_CHECKIN_TIME", "reason": "The event has already ended."})

        ticket.status = "used"
        ticket.used_at = now
        db.commit()
        self.incr_state(db, "admitted_total")
        return ("AGREE", {"result": "ADMIT", "reason": "Ticket is valid.", "event": event.title})