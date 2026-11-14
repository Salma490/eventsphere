from app.agents.base import BaseAgent
from app.models import Notification, User


class NotificationAgent(BaseAgent):
    """Goal: tell the right person what affects them.
    Rules:
      - 'notify' -> one notification for the affected user only
      - 'notify_admins' -> one notification for every admin (escalations, suspicious scans)
      - broadcast 'event_cancelled' -> admins are told
    """
    name = "NotificationAgent"

    def _add(self, db, user_id, text):
        db.add(Notification(user_id=user_id, text=text[:300]))
        db.commit()
        self.incr_state(db, "notifications_sent")

    def on_notify(self, db, msg):
        self._add(db, msg.content["user_id"], msg.content["text"])
        return None

    def on_notify_admins(self, db, msg):
        for admin in db.query(User).filter_by(role="admin").all():
            self._add(db, admin.id, msg.content["text"])
        return None

    def on_event_cancelled(self, db, msg):
        for admin in db.query(User).filter_by(role="admin").all():
            self._add(db, admin.id, f"Event '{msg.content.get('title')}' was cancelled and all bookings refunded.")
        return None