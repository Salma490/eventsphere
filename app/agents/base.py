from app.models import AgentState

class BaseAgent:
    name = "BaseAgent"

    def handle(self, db, msg):
        """Find the method that matches the message (on_<action> or on_<event>)."""
        action = msg.content.get("action") or msg.content.get("event")
        method = getattr(self, f"on_{action}", None)
        if method is None:
            if msg.performative == "INFORM":
                return None  # not interested in this broadcast
            return ("FAILURE", {"reason": f"{self.name} does not understand '{action}'"})
        return method(db, msg)

    # ---- persistent beliefs (saved in the database, survive restarts) ----
    def get_state(self, db, key, default=""):
        row = db.query(AgentState).filter_by(agent_name=self.name, key=key).first()
        return row.value if row else default

    def set_state(self, db, key, value):
        row = db.query(AgentState).filter_by(agent_name=self.name, key=key).first()
        if row:
            row.value = str(value)
        else:
            db.add(AgentState(agent_name=self.name, key=key, value=str(value)))
        db.commit()

    def incr_state(self, db, key, by=1):
        n = int(self.get_state(db, key, "0")) + by
        self.set_state(db, key, n)
        return n