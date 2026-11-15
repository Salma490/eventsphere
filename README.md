# EventSphere

A mobile-friendly venue booking and digital ticketing platform in which every booking decision is made by cooperating software agents.
IT4123 Agent Based Computing Practical, ICA 02.

**Live URL:** https://eventsphere-1-0vw2.onrender.com
**GitHub:** https://github.com/Salma490/eventsphere

_(Hosted on a free Render instance, so the first visit after a quiet period can take about a minute to wake up.)_

## Technology

Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL (production, hosted on Render), SQLite (local development), Jinja2 templates with Bootstrap 5, `qrcode` (QR generation), `html5-qrcode` (camera scanning), pytest.

## The five agents

| Agent             | Goal                                                                           |
| ----------------- | ------------------------------------------------------------------------------ |
| VenueAgent        | Keep every venue calendar free of clashes; decide and price private hires      |
| BookingAgent      | Sell tickets fairly without overselling; refunds                               |
| TicketAgent       | Issue unique codes; admit each ticket exactly once                             |
| WaitlistAgent     | Pass freed seats to waiting customers with timed holds (30 second sweep timer) |
| NotificationAgent | Turn events from other agents into user notifications                          |

Agents only communicate through JSON messages (sender, receiver, performative, content, conversation id) sent over a message bus (`app/bus.py`). Every message is stored and shown on the admin page `/admin/messages`.

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

## Environment variables

| Variable         | Purpose                                                      | Default                      |
| ---------------- | ------------------------------------------------------------ | ---------------------------- |
| `DATABASE_URL`   | Database connection string (PostgreSQL in production)        | `sqlite:///./eventsphere.db` |
| `SECRET_KEY`     | Signs session cookies. Set a long random value in production | development-only value       |
| `TZ`             | Server time zone (production uses `Asia/Colombo`)            | system time zone             |
| `PYTHON_VERSION` | Python version used by the host (production uses 3.12.10)    | not needed locally           |

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

21 tests covering overbooking, double check-in, wrong-event and cancelled tickets, venue clashes and alternatives, refunds, waitlist expiry, notifications, message tracing, role checks and password hashing. Tests use a separate `test_eventsphere.db`.

## Security and design notes

- Passwords are hashed with bcrypt. Admin pages and the scanner check the user role on every route. Every form carries a CSRF token.
- Seats are taken with one atomic `UPDATE ... WHERE remaining >= quantity`, so two simultaneous buyers can never oversell the last seat.
- Payments are simulated. A payment reference is generated and no card details are collected.
