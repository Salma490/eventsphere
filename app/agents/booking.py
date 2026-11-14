import secrets
from datetime import datetime
from sqlalchemy import update, func
from app import bus
from app.agents.base import BaseAgent
from app.models import Event, TicketCategory, Booking

MAX_PER_REQUEST = 6
MAX_PER_USER_PER_EVENT = 6
FULL_REFUND_HOURS = 7 * 24
HALF_REFUND_HOURS = 24

def _release_seats(db, category_id, qty):
    db.execute(update(TicketCategory).where(TicketCategory.id == category_id)
               .values(remaining=TicketCategory.remaining + qty))
    db.commit()


class BookingAgent(BaseAgent):
    """Goal: sell tickets fairly without overselling.
    Rules:
      1. Refuse if quantity < 1 or > 6 per request.
      2. Refuse if the event is not published or has already started.
      3. Refuse if the user would hold more than 6 tickets for the event.
      4. Refuse if not enough tickets remain (reply says the user can join the waitlist).
         Seats are taken with ONE atomic database UPDATE ("remaining = remaining - qty
         WHERE remaining >= qty"), so two simultaneous buyers can never oversell.
      5. Cancellation refund: >= 7 days before start = 100%, 1-7 days = 50%, < 24 hours = 0%.
      6. Cancelling after the event started is refused.
      7. If an event is cancelled, every booking gets a 100% refund.
      8. Freed seats are announced to the WaitlistAgent.
    """
    name = "BookingAgent"

    def on_book(self, db, msg):
        c = msg.content
        qty = int(c.get("quantity", 0))
        from_hold = bool(c.get("from_hold", False))  # seats already reserved by the waitlist

        if qty < 1 or qty > MAX_PER_REQUEST:
            return ("REFUSE", {"reason": f"You can book 1 to {MAX_PER_REQUEST} tickets per request."})
        event = db.get(Event, c["event_id"])
        category = db.get(TicketCategory, c["category_id"])
        if event is None or category is None or category.event_id != event.id:
            return ("REFUSE", {"reason": "Event or ticket category not found."})
        if event.status != "published":
            return ("REFUSE", {"reason": "This event is not open for booking."})
        if datetime.now() >= event.start_time:
            return ("REFUSE", {"reason": "This event has already started."})

        already = db.query(func.coalesce(func.sum(Booking.quantity), 0)).filter(
            Booking.user_id == c["user_id"], Booking.event_id == event.id,
            Booking.status == "confirmed").scalar()
        if already + qty > MAX_PER_USER_PER_EVENT:
            return ("REFUSE", {"reason": f"Limit is {MAX_PER_USER_PER_EVENT} tickets per person for this event "
                                         f"(you already have {already})."})

        if not from_hold:
            result = db.execute(
                update(TicketCategory)
                .where(TicketCategory.id == category.id, TicketCategory.remaining >= qty)
                .values(remaining=TicketCategory.remaining - qty))
            rowcount = result.rowcount
            db.commit()
            if rowcount == 0:
                db.refresh(category)
                return ("REFUSE", {"reason": f"Not enough tickets left (only {category.remaining} available).",
                                   "can_waitlist": True, "tickets_left": category.remaining})

        booking = Booking(user_id=c["user_id"], event_id=event.id, category_id=category.id,
                          quantity=qty, amount=round(category.price * qty, 2),
                          payment_ref="PAY-" + secrets.token_hex(5).upper(), status="confirmed")
        db.add(booking)
        db.commit()

        reply = bus.send(db, self.name, "TicketAgent", "REQUEST",
                         {"action": "issue_tickets", "booking_id": booking.id}, msg.conversation_id)
        if not reply or reply["performative"] != "AGREE":
            booking.status = "cancelled"
            db.commit()
            if not from_hold:
                _release_seats(db, category.id, qty)
            return ("FAILURE", {"reason": "Tickets could not be issued, so the booking was undone."})

        bus.send(db, self.name, "NotificationAgent", "INFORM",
                 {"event": "notify", "user_id": booking.user_id,
                  "text": f"Booking #{booking.id} confirmed: {qty} x {category.name} for {event.title}. "
                          f"Payment ref {booking.payment_ref}."}, msg.conversation_id)
        return ("AGREE", {"booking_id": booking.id, "payment_ref": booking.payment_ref,
                          "amount": booking.amount})

    def on_cancel_booking(self, db, msg):
        c = msg.content  # {"booking_id", "user_id", "is_admin" (optional)}
        booking = db.get(Booking, c["booking_id"])
        if booking is None:
            return ("REFUSE", {"reason": "Booking not found."})
        if booking.user_id != c["user_id"] and not c.get("is_admin"):
            return ("REFUSE", {"reason": "This is not your booking."})
        if booking.status != "confirmed":
            return ("REFUSE", {"reason": "This booking is already cancelled."})
        event = db.get(Event, booking.event_id)
        now = datetime.now()
        if now >= event.start_time:
            return ("REFUSE", {"reason": "The event has already started, so it cannot be cancelled."})

        hours_left = (event.start_time - now).total_seconds() / 3600
        percent = 100 if hours_left >= FULL_REFUND_HOURS else 50 if hours_left >= HALF_REFUND_HOURS else 0
        refund = round(booking.amount * percent / 100, 2)

        booking.status = "cancelled"
        booking.refund_amount = refund
        db.commit()
        _release_seats(db, booking.category_id, booking.quantity)

        bus.send(db, self.name, "TicketAgent", "REQUEST",
                 {"action": "cancel_tickets", "booking_id": booking.id}, msg.conversation_id)
        bus.send(db, self.name, "NotificationAgent", "INFORM",
                 {"event": "notify", "user_id": booking.user_id,
                  "text": f"Booking #{booking.id} cancelled. Refund: LKR {refund:.2f} ({percent}%)."},
                 msg.conversation_id)
        bus.send(db, self.name, "WaitlistAgent", "INFORM",
                 {"event": "seats_freed", "event_id": booking.event_id,
                  "category_id": booking.category_id, "quantity": booking.quantity},
                 msg.conversation_id)
        return ("AGREE", {"refund": refund, "percent": percent})

    def on_cancel_event(self, db, msg):
        event = db.get(Event, msg.content["event_id"])
        if event is None:
            return ("REFUSE", {"reason": "Event not found."})
        event.status = "cancelled"
        db.commit()
        total, count = 0.0, 0
        for b in db.query(Booking).filter_by(event_id=event.id, status="confirmed").all():
            b.status = "cancelled"
            b.refund_amount = b.amount
            total += b.amount
            count += 1
            db.commit()
            bus.send(db, self.name, "TicketAgent", "REQUEST",
                     {"action": "cancel_tickets", "booking_id": b.id}, msg.conversation_id)
            bus.send(db, self.name, "NotificationAgent", "INFORM",
                     {"event": "notify", "user_id": b.user_id,
                      "text": f"'{event.title}' was cancelled. Full refund of LKR {b.amount:.2f} issued."},
                     msg.conversation_id)
        bus.send(db, self.name, "ALL", "INFORM",
                 {"event": "event_cancelled", "event_id": event.id, "title": event.title},
                 msg.conversation_id)
        return ("AGREE", {"refunded_bookings": count, "total_refunded": round(total, 2)})