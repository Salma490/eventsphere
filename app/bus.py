import uuid
from typing import Literal
from pydantic import BaseModel
from app.models import Message

AGENTS = {}  # filled at startup: name -> agent object

Performative = Literal["REQUEST", "AGREE", "REFUSE", "PROPOSE", "INFORM", "FAILURE"]

class AgentMessage(BaseModel):
    """Pydantic validation of every message that goes on the bus."""
    conversation_id: str
    sender: str
    receiver: str
    performative: Performative
    content: dict

def new_conversation_id() -> str:
    return "c-" + uuid.uuid4().hex[:8]

def _log(db, m: AgentMessage):
    row = Message(**m.model_dump())
    db.add(row)
    db.commit()
    return row

def send(db, sender, receiver, performative, content, conversation_id=None):
    """Send a message. Every message is logged. Returns the reply as a dict
    (or None for broadcasts and one-way INFORM messages)."""
    cid = conversation_id or new_conversation_id()
    m = AgentMessage(conversation_id=cid, sender=sender, receiver=receiver, performative=performative, content=content)
    row = _log(db, m)

    if receiver == "ALL":  # broadcast: every agent except the sender listens
        for name, agent in list(AGENTS.items()):
            if name != sender:
                agent.handle(db, row)
        return None

    agent = AGENTS.get(receiver)
    if agent is None:
        raise ValueError(f"Unknown receiver: {receiver}")
    result = agent.handle(db, row)
    if result is None:
        return None

    perf, body = result
    _log(db, AgentMessage(conversation_id=cid, sender=receiver, receiver=sender, performative=perf, content=body))
    return {"performative": perf, "content": body, "conversation_id": cid}