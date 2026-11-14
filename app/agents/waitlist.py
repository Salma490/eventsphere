from datetime import datetime, timedelta
from sqlalchemy import update
from app import bus
from app.agents.base import BaseAgent
from app.models import Event, TicketCategory, WaitlistEntry

HOLD_MINUTES = 10
MAX_QTY = 6

class WaitlistAgent(BaseAgent):
    """Goal: pass freed seats on to waiting customers.
    Rules:
      1. Join is refused if seats are available (book directly), the event is not open,
         quantity is not 1-6, or the user is already waiting/holding for that category.
      2. When seats are freed, the FIRST waiting customer (oldest first) whose quantity
         fits gets the seats HELD for 10 minutes. Held seats are removed from sale.
      3. A claim is refused if the hold has expired or belongs to someone else.
      4. PROACTIVE: every 30 seconds tick() looks for expired holds, returns the seats,
         tells the customer, and offers the seats to the next person in the queue.
      5. If the event is cancelled (broadcast), all waiting/held entries are closed.
    State: holds_offered, expired_holds, last_sweep.
    """
    name = "WaitlistAgent"

    def on_join(self, db, msg):
        c = msg.content
        qty = int(c.get("quantity", 1))
        event = db.get(Event, c["event_id"])
        cat = db.get(TicketCategory, c["category_id"])
        if event is None or cat is None or cat.event_id != event.id:
            return ("REFUSE", {"reason": "Event or category not found."})
        if event.status != "published" or datetime.now() >= event.start_time:
            return ("REFUSE", {"reason": "This event is not open for booking."})
        if qty < 1 or qty > MAX_QTY:
            return ("REFUSE", {"reason": f"You can wait for 1 to {MAX_QTY} tickets."})
        if cat.remaining >= qty:
            return ("REFUSE", {"reason": "Tickets are available right now, please book directly."})
        existing = db.query(WaitlistEntry).filter(
            WaitlistEntry.user_id == c["user_id"], WaitlistEntry.category_id == cat.id,
            WaitlistEntry.status.in_(["waiting", "held"])).first()
        if existing:
            return ("REFUSE", {"reason": "You are already on the waitlist for this category."})
        entry = WaitlistEntry(event_id=event.id, category_id=cat.id, user_id=c["user_id"], quantity=qty)
        db.add(entry)
        db.commit()
        position = db.query(WaitlistEntry).filter(
            WaitlistEntry.category_id == cat.id, WaitlistEntry.status == "waiting",
            WaitlistEntry.id <= entry.id).count()
        return ("AGREE", {"waitlist_id": entry.id, "position": position})

    def on_seats_freed(self, db, msg):
        self._offer_next(db, msg.content["event_id"], msg.content["category_id"], msg.conversation_id)
        return None

    def on_claim(self, db, msg):
        c = msg.content  # {"waitlist_id", "user_id"}
        entry = db.get(WaitlistEntry, c["waitlist_id"])
        if entry is None or entry.user_id != c["user_id"]:
            return ("REFUSE", {"reason": "Waitlist entry not found."})
        if entry.status != "held":
            return ("REFUSE", {"reason": "You do not have a seat on hold."})
        if entry.hold_expires_at < datetime.now():
            return ("REFUSE", {"reason": "Your hold has expired."})
        reply = bus.send(db, self.name, "BookingAgent", "REQUEST",
                         {"action": "book", "user_id": entry.user_id, "event_id": entry.event_id,
                          "category_id": entry.category_id, "quantity": entry.quantity,
                          "from_hold": True}, msg.conversation_id)
        if reply and reply["performative"] == "AGREE":
            entry.status = "booked"
            db.commit()
            return ("AGREE", reply["content"])
        entry.status = "expired"  # booking failed: give the seats back
        db.execute(update(TicketCategory).where(TicketCategory.id == entry.category_id)
                   .values(remaining=TicketCategory.remaining + entry.quantity))
        db.commit()
        reason = reply["content"].get("reason", "Booking failed.") if reply else "Booking failed."
        return ("REFUSE", {"reason": reason})

    def on_event_cancelled(self, db, msg):
        entries = db.query(WaitlistEntry).filter(
            WaitlistEntry.event_id == msg.content["event_id"],
            WaitlistEntry.status.in_(["waiting", "held"])).all()
        for e in entries:
            e.status = "expired"
        db.commit()
        return None

    # ---------- proactive behaviour (no user action, no incoming message) ----------
    def tick(self, db):
        """Called by a background timer every 30 seconds."""
        cid = bus.new_conversation_id()
        now = datetime.now()
        self.set_state(db, "last_sweep", now.isoformat(timespec="seconds"))
        expired = db.query(WaitlistEntry).filter(
            WaitlistEntry.status == "held", WaitlistEntry.hold_expires_at < now).all()
        pairs = set()
        for e in expired:
            e.status = "expired"
            db.commit()
            db.execute(update(TicketCategory).where(TicketCategory.id == e.category_id)
                       .values(remaining=TicketCategory.remaining + e.quantity))
            db.commit()
            self.incr_state(db, "expired_holds")
            bus.send(db, self.name, "NotificationAgent", "INFORM",
                     {"event": "notify", "user_id": e.user_id,
                      "text": "Your 10-minute seat hold expired and the seats were offered to the next person."},
                     cid)
            pairs.add((e.event_id, e.category_id))
        for event_id, category_id in pairs:
            self._offer_next(db, event_id, category_id, cid)
        return len(expired)

    def _offer_next(self, db, event_id, category_id, cid):
        while True:
            waiting = db.query(WaitlistEntry).filter_by(
                event_id=event_id, category_id=category_id, status="waiting"
            ).order_by(WaitlistEntry.id).all()
            offered = False
            for entry in waiting:
                result = db.execute(
                    update(TicketCategory)
                    .where(TicketCategory.id == category_id, TicketCategory.remaining >= entry.quantity)
                    .values(remaining=TicketCategory.remaining - entry.quantity))
                rowcount = result.rowcount
                db.commit()
                if rowcount == 1:
                    entry.status = "held"
                    entry.hold_expires_at = datetime.now() + timedelta(minutes=HOLD_MINUTES)
                    db.commit()
                    self.incr_state(db, "holds_offered")
                    title = db.get(Event, event_id).title
                    bus.send(db, self.name, "NotificationAgent", "INFORM",
                             {"event": "notify", "user_id": entry.user_id,
                              "text": f"Seats are available for {title}! Claim them within "
                                      f"{HOLD_MINUTES} minutes."}, cid)
                    offered = True
                    break
            if not offered:
                return