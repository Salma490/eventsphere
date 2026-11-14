from app import bus
from app.agents.venue import VenueAgent
from app.agents.booking import BookingAgent
from app.agents.ticket import TicketAgent
from app.agents.waitlist import WaitlistAgent
from app.agents.notification import NotificationAgent

def register_all():
    for cls in (VenueAgent, BookingAgent, TicketAgent, WaitlistAgent, NotificationAgent):
        agent = cls()
        bus.AGENTS[agent.name] = agent