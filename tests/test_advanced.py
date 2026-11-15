from datetime import timedelta
from fastapi.testclient import TestClient

from app.main import app


def get_token(client, url):
    html = client.get(url).text
    return html.split('name="csrf_token" value="')[1].split('"')[0]


def login(client, email, password):
    token = get_token(client, "/login")
    return client.post("/login", data={"email": email, "password": password, "csrf_token": token},
                       follow_redirects=False)


def test_agent_inbox_needs_key(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-test-key")
    with TestClient(app) as client:
        r = client.post("/agents/VenueAgent/inbox",
                        json={"performative": "REQUEST", "content": {"action": "check_slot"}})
    assert r.status_code == 401


def test_agent_inbox_delivers_message(db, world, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "secret-test-key")
    start = world["event"].start_time
    with TestClient(app) as client:
        r = client.post("/agents/VenueAgent/inbox", headers={"X-Agent-Key": "secret-test-key"},
                        json={"sender": "ExternalClient", "performative": "REQUEST",
                              "content": {"action": "check_slot", "venue_id": world["venue"].id,
                                          "start": start.isoformat(),
                                          "end": (start + timedelta(hours=1)).isoformat()}})
    assert r.status_code == 200
    assert r.json()["performative"] == "REFUSE"   # clashes with the concert


def test_advanced_search_price_and_venue(db, world):
    with TestClient(app) as client:
        def shown(url):
            return "View and book" in client.get(url).text   # an event card is on the page

        assert shown("/?max_price=600")                          # event costs 500: inside range
        assert not shown("/?min_price=600")                      # outside range
        assert shown(f"/?venue_id={world['venue'].id}")          # right venue
        assert not shown("/?venue_id=9999")                      # other venue


def test_dashboard_shows_charts(db, world):
    with TestClient(app) as client:
        login(client, "admin@eventsphere.lk", "Admin@123")
        r = client.get("/admin")
    assert r.status_code == 200 and "salesChart" in r.text