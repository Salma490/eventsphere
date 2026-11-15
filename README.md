# EventSphere

A mobile-friendly venue booking and digital ticketing platform in which every booking decision is made by cooperating software agents.
IT4123 Agent Based Computing Practical, ICA 02.

**Live URL:** https://eventsphere-1-0vw2.onrender.com
**API documentation:** https://eventsphere-1-0vw2.onrender.com/docs
**GitHub:** https://github.com/Salma490/eventsphere

_(Hosted on a free Render instance, so the first visit after a quiet period can take about a minute to wake up.)_

## Technology

Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL (production, hosted on Render), SQLite (local development), Jinja2 templates with Bootstrap 5, Chart.js (dashboard charts), `qrcode` (QR generation), `html5-qrcode` (camera scanning), pytest.

## The five agents

| Agent             | Goal                                                                           |
| ----------------- | ------------------------------------------------------------------------------ |
| VenueAgent        | Keep every venue calendar free of clashes; decide and price private hires      |
| BookingAgent      | Sell tickets fairly without overselling; refunds                               |
| TicketAgent       | Issue unique codes; admit each ticket exactly once                             |
| WaitlistAgent     | Pass freed seats to waiting customers with timed holds (30 second sweep timer) |
| NotificationAgent | Turn events from other agents into user notifications                          |

Agents only communicate through JSON messages (sender, receiver, performative, content, conversation id) sent over a message bus (`app/bus.py`). Every message is stored and shown on the admin page `/admin/messages`. Each agent's decision rules are listed in the project report.

## Advanced features (Part C)

- Waitlist with timed seat holds, a visible countdown and automatic re-offer (30 second sweep)
- Weekend pricing for venue hire, decided by the VenueAgent (+25% on Saturday and Sunday)
- Detection of suspicious repeated scans of the same ticket (admins are notified from the third scan)
- Agents reachable over HTTP: `GET /agents` and `POST /agents/{agent_name}/inbox` (see below)
- Dashboard charts: tickets and revenue per event, check-ins per hour
- Advanced search: venue, date range and price range filters

## Agent HTTP inbox

Each agent can receive a JSON message over HTTP. The message goes through the same message bus, so it is logged on the admin message page like every other message. The endpoint is disabled unless the `AGENT_API_KEY` environment variable is set, and each request must send that key in the `X-Agent-Key` header.

```
curl -X POST https://eventsphere-1-0vw2.onrender.com/agents/VenueAgent/inbox \
  -H "Content-Type: application/json" \
  -H "X-Agent-Key: YOUR_KEY" \
  -d '{"sender": "ExternalClient", "performative": "REQUEST",
       "content": {"action": "check_slot", "venue_id": 1,
                   "start": "2030-01-01T10:00:00", "end": "2030-01-01T12:00:00"}}'
```

`GET /agents` lists every agent and the actions it accepts. Interactive documentation is at `/docs`.

## Run locally

```
git clone https://github.com/Salma490/eventsphere.git
cd eventsphere
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. A local SQLite file (`eventsphere.db`) is created automatically, and the demo accounts and sample venues are added on first start.

To try the agent inbox locally, set a key first in the same terminal (Command Prompt: `set AGENT_API_KEY=demo-key-123`, PowerShell: `$env:AGENT_API_KEY="demo-key-123"`), then start the server.

## Environment variables

| Variable         | Purpose                                                                      | Default                      |
| ---------------- | ---------------------------------------------------------------------------- | ---------------------------- |
| `DATABASE_URL`   | Database connection string (PostgreSQL in production)                        | `sqlite:///./eventsphere.db` |
| `SECRET_KEY`     | Signs session cookies. Set a long random value in production                 | development-only value       |
| `AGENT_API_KEY`  | Key required by the agent HTTP inbox. The inbox is disabled if it is not set | not set                      |
| `TZ`             | Server time zone (production uses `Asia/Colombo`)                            | system time zone             |
| `PYTHON_VERSION` | Python version used by the host (production uses 3.12.10)                    | not needed locally           |

No secrets are committed to Git. They are set as environment variables on the host.

## Demo accounts

| Role     | Email                   | Password     |
| -------- | ----------------------- | ------------ |
| Admin    | admin@eventsphere.lk    | Admin@123    |
| Staff    | staff@eventsphere.lk    | Staff@123    |
| Attendee | attendee@eventsphere.lk | Attendee@123 |

## Run the tests

```
python -m pytest -v
```

25 tests covering overbooking, double check-in, wrong-event and cancelled tickets, venue clashes and alternatives, refunds, waitlist expiry, notifications, message tracing, role checks, password hashing, the agent HTTP inbox, advanced search and the dashboard charts. Tests use a separate `test_eventsphere.db`.

## Security and design notes

- Passwords are hashed with bcrypt. Admin pages and the scanner check the user role on every route. Every form carries a CSRF token.
- Seats are taken with one atomic `UPDATE ... WHERE remaining >= quantity`, so two simultaneous buyers can never oversell the last seat.
- Payments are simulated. A payment reference is generated and no card details are collected.
