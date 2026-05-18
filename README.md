# HEXA Transmission Connectivity Agent

An automation agent that **scrapes available transmission connectivity records
from a government website (CTUIL by default) and emails you a formatted
report every day at 11 PM**.

By default it targets the Central Transmission Utility of India Limited
("Connectivity to be Made Effective"):

> <https://www.ctuil.in/connectivity-effective-list>

Each daily report contains:

- The full current snapshot (with a preview of the first 25 rows).
- **What changed since yesterday** — added and removed connectivity entries.
- Region / state / generation-type filters (optional).
- A clean HTML email plus a plain-text fallback.

The same Python package can be deployed three ways:

1. **Long-running scheduler** (`python main.py schedule`) — fires daily at 23:00.
2. **One-shot run** (`python main.py run`) — pair with cron / Task Scheduler.
3. **GitHub Actions** — zero-server option, `.github/workflows/daily-mail.yml`.

A `Dockerfile`, `docker-compose.yml` and a `systemd` unit are also included.

There is **also a local Flask web dashboard** at `webapp/` you can open in
your browser to scrape on demand, preview the email body live, and send
a one-off test mail without waiting for the 11 PM trigger. See
[Local web dashboard](#local-web-dashboard) below.

## See what it looks like (no install required)

Pre-rendered samples ship with the repo so you can preview everything in
your browser before configuring SMTP:

- **`samples/email-preview.html`** — open in any browser to see the exact
  HTML email body that gets sent (full real data, 565 records).
- **`samples/email-preview.txt`** — plain-text fallback body.
- **`samples/email-subject.txt`** — the subject line.
- **`samples/snapshot.json`** — the full real-life scrape (565 unique
  records across 65 pages of `ctuil.in/connectivity-effective-list`).
- **`samples/screenshots/`** — PNGs of the email and the localhost
  dashboard:
  - `email-preview.png`
  - `dashboard-empty.png`
  - `dashboard-with-data.png`

To regenerate the samples against the live CTUIL site:

```bash
python scripts/build_sample.py
```

---

## Quick start

```bash
git clone <this-repo>
cd HEXA-Automation

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env – at minimum set SMTP_USERNAME, SMTP_PASSWORD, MAIL_FROM, MAIL_TO

# 1. Verify scraping works (no email):
python main.py scrape --limit 5

# 2. Verify SMTP works (one tiny email):
python main.py test-mail

# 3. One full run (scrape + diff + email + persist snapshot):
python main.py run

# 4. Long-running scheduler (daily 23:00 IST by default):
python main.py schedule

# 5. (Optional) Open the localhost dashboard:
python -m webapp.app    # then visit http://localhost:5000
```

If you want the scheduler to also fire one run on startup (useful for the
very first deployment so you immediately get a baseline email):

```bash
python main.py schedule --run-now
```

---

## Configuring email (Gmail example)

Gmail blocks plain-password SMTP logins. Use an **App Password**:

1. Enable 2-Step Verification on the Google account.
2. Visit <https://myaccount.google.com/apppasswords>, generate an app
   password for "Mail" (16 characters, no spaces).
3. In `.env`:

   ```ini
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USE_TLS=true
   SMTP_USERNAME=your.bot@gmail.com
   SMTP_PASSWORD=xxxxxxxxxxxxxxxx     # the 16-char app password
   MAIL_FROM=HEXA Connectivity Bot <your.bot@gmail.com>
   MAIL_TO=you@example.com,manager@example.com
   ```

Other providers work out of the box too:

| Provider  | `SMTP_HOST`             | `SMTP_PORT` | `SMTP_USE_TLS` |
| --------- | ----------------------- | ----------- | -------------- |
| Gmail     | `smtp.gmail.com`        | `587`       | `true`         |
| Outlook   | `smtp-mail.outlook.com` | `587`       | `true`         |
| Zoho      | `smtp.zoho.in`          | `587`       | `true`         |
| SendGrid  | `smtp.sendgrid.net`     | `587`       | `true`         |
| Amazon SES| `email-smtp.<region>.amazonaws.com` | `587` | `true`  |
| (TLS-465) | `smtp.example.com`      | `465`       | `true` (uses SSL) |

Run `python main.py test-mail` to confirm the SMTP path before scheduling.

---

## Configuration reference

All settings come from environment variables (or an `.env` file at the
project root). See [`.env.example`](.env.example) for the full list.

| Variable             | Default                                                | Purpose                                                |
| -------------------- | ------------------------------------------------------ | ------------------------------------------------------ |
| `SOURCE_URL`         | `https://www.ctuil.in/connectivity-effective-list`     | Government page to scrape                              |
| `MAX_PAGES`          | `0` (= unlimited)                                      | Cap on paginated pages crawled                         |
| `FILTER_REGION`      | *(empty)*                                              | Substring filter, e.g. `NR`                            |
| `FILTER_STATE`       | *(empty)*                                              | Substring filter, e.g. `Rajasthan`                     |
| `FILTER_TYPE`        | *(empty)*                                              | Substring filter, e.g. `Solar`                         |
| `SMTP_HOST/PORT/...` | Gmail defaults                                         | SMTP credentials                                       |
| `MAIL_FROM`          | *(required)*                                           | `From:` header                                         |
| `MAIL_TO`            | *(required)*                                           | Comma-separated recipients                             |
| `RUN_HOUR`           | `23`                                                   | Hour-of-day for the scheduled job                      |
| `RUN_MINUTE`         | `0`                                                    | Minute-of-hour                                         |
| `TIMEZONE`           | `Asia/Kolkata`                                         | IANA zone for the schedule + timestamps in the report  |
| `DATA_DIR`           | `./data`                                               | Where the previous snapshot is stored (for diffing)    |
| `DRY_RUN`            | `false`                                                | If `true`, skip sending mail                           |
| `SEND_ON_NO_CHANGE`  | `true`                                                 | Email anyway when nothing changed since yesterday      |

---

## Deployment options

### Option A – Run-as-a-service (long-running scheduler)

The simplest setup: keep one Python process alive and let APScheduler
trigger the daily 23:00 job.

```bash
python main.py schedule
```

To run it as a `systemd` service on Linux:

```bash
sudo useradd -r -s /bin/false hexa
sudo mkdir -p /opt/hexa-connectivity-agent
sudo chown hexa:hexa /opt/hexa-connectivity-agent
# ... copy the repo to /opt/hexa-connectivity-agent and create .venv there ...
sudo cp deploy/hexa-connectivity-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hexa-connectivity-agent
sudo journalctl -u hexa-connectivity-agent -f
```

See [`deploy/hexa-connectivity-agent.service`](deploy/hexa-connectivity-agent.service).

### Option B – Cron / Task Scheduler (one-shot)

If you prefer the OS scheduler:

```cron
# /etc/cron.d/hexa-connectivity-agent  – runs daily at 23:00 server time.
0 23 * * *  hexa  cd /opt/hexa-connectivity-agent && .venv/bin/python main.py run >> /var/log/hexa.log 2>&1
```

On Windows, use **Task Scheduler** with the action:

```
Program: C:\Python312\python.exe
Args:    C:\path\to\HEXA-Automation\main.py run
Start in:C:\path\to\HEXA-Automation
```

Schedule it daily at 23:00.

### Option C – Docker

```bash
docker compose up -d --build
docker compose logs -f
```

The container's entry point is `python main.py schedule`, so it stays
alive and fires the job daily at the time configured in `.env`.

### Local / self-hosted web dashboard

The same web UI that powers the Render deploy can run on your laptop or
any server:

```bash
python -m webapp.app                 # dev server, http://localhost:5000
# or for production / Render:
gunicorn -w 1 -k gthread --threads 4 -b 0.0.0.0:$PORT webapp.app:app
```

This single process serves the dashboard **and** runs the embedded
scheduler (so it doubles as `python main.py schedule`).

What the dashboard gives you:

- **KPI cards**: previous snapshot, live count, day-over-day diff, last
  scheduled run outcome.
- **▶ Send email now** — the big green "Start" button: scrapes
  immediately, builds the email, and sends it to all configured
  recipients in one click.
- **Settings panel** — edit everything from the browser; saved to
  `data/settings.json`, picked up by the scheduler without a restart:
  - Daily time (24-hour clock) + timezone
  - Schedule on/off toggle, dry-run toggle
  - Source URL, region/state/type filters, max-pages cap
  - SMTP host / port / STARTTLS / username / password
  - From address and recipients (comma-separated, multiple)
- **Live data preview** — sortable, filterable table of the current scrape.
- **Email preview** — HTML body, plain-text body, and the newsletter
  JSON payload (`/api/newsletter.json`) side by side.
- **Send test email** — sends a `[TEST]`-prefixed copy using the saved
  SMTP settings (handy to verify Gmail App Passwords before going live).
- **Save as 'previous snapshot'** — manually commit the cached scrape so
  tomorrow's diff is meaningful for demos.

See `samples/screenshots/dashboard-with-data.png` for a preview.

#### Newsletter-style JSON output

`GET /api/newsletter.json` returns a structured payload (schema
`hexa.transmission-connectivity.v1`) — useful if you want to pipe the
data into another newsletter service, Slack, or a static site. A
pre-generated full-data example lives in `samples/newsletter.json`.

```json
{
  "schema": "hexa.transmission-connectivity.v1",
  "title": "Daily Transmission Connectivity Report",
  "generated_at": "2026-05-19T01:15:15+05:30",
  "subject": "Transmission Connectivity – 2026-05-19 | 565 records | +25/-0",
  "source": {
    "name": "CTUIL – Central Transmission Utility of India Limited",
    "url": "https://www.ctuil.in/connectivity-effective-list",
    "pages_scraped": 65
  },
  "summary": { "total_records": 565, "new_count": 25, "removed_count": 0, "unchanged_count": 540 },
  "sections": [
    { "id": "new",      "type": "table", "rows": [ ... ] },
    { "id": "snapshot", "type": "table", "rows": [ ... ] }
  ]
}
```

### Option D – Render.com (one-click web service)

[Render](https://render.com) hosts both the dashboard and the scheduler in
**one** web service — same process, persistent disk, free TLS cert.

1. Fork this repo (or push it to your own GitHub).
2. On <https://dashboard.render.com> → **New +** → **Blueprint** → connect
   the repo. Render reads [`render.yaml`](render.yaml) and provisions:

   - a `web` service running `gunicorn -w 1 -k gthread webapp.app:app`
   - a 1 GB persistent disk mounted at `/var/data` (holds
     `settings.json` and `last_snapshot.json`)

3. Once it's up, open the service URL and the dashboard appears.
4. Click **Settings**, fill in SMTP host/port/username/password, the
   sender, the recipients, and the daily run time. Hit **Save settings**.
5. (Optional) Click **▶ Send email now** for an immediate first delivery.

That's it — at 23:00 in your configured timezone every day, the embedded
scheduler runs `scrape → diff → render → email → persist`.

**Notes on plans / wake-up:**

- **Starter ($7/mo)** keeps the container always-on, so the 11 PM trigger
  fires reliably.
- **Free plan** suspends the service after 15 min of inactivity. Either:
  - Use a free uptime monitor (e.g. [cron-job.org](https://cron-job.org),
    [UptimeRobot](https://uptimerobot.com)) to ping
    `https://<your-service>.onrender.com/healthz` every 5 minutes, or
  - Switch to **Option E** (GitHub Actions) which is fully serverless and
    free — see below.

### Option E – GitHub Actions (no server)

Open the repo on GitHub and configure:

- **Variables** (Settings → Secrets and variables → Actions → Variables)
  `MAIL_FROM`, `MAIL_TO`, `TIMEZONE`, optional `FILTER_REGION`, etc.
- **Secrets** (Settings → Secrets and variables → Actions → Secrets)
  `SMTP_USERNAME`, `SMTP_PASSWORD`.

The provided workflow [`.github/workflows/daily-mail.yml`](.github/workflows/daily-mail.yml)
runs at **17:30 UTC = 23:00 IST** every day, persists the previous
snapshot via the Actions cache, and can also be triggered manually
("Run workflow" button).

---

## What the email looks like

The HTML body contains:

- A header pill row: **Total today**, **New**, **Removed**, **Unchanged**.
- A **New entries** table (green background) — added since yesterday.
- A **Removed** table (red background) — entries that disappeared.
- A **Current snapshot** table — the first 25 records of today's data.

The plain-text alternative carries the same information in a simple
bullet-list layout for terminals and mail clients that don't render HTML.

---

## Project layout

```
.
├── hexa_agent/
│   ├── __init__.py
│   ├── agent.py        # orchestrator (scrape → diff → render → email)
│   ├── config.py       # .env / env-var loading + validation
│   ├── mailer.py       # SMTP send (TLS / SSL)
│   ├── report.py       # HTML + text rendering (Jinja2)
│   ├── scheduler.py    # APScheduler daily trigger
│   ├── scraper.py      # CTUIL HTML parser (BeautifulSoup + lxml)
│   └── storage.py      # JSON snapshot + diffing
├── webapp/
│   ├── app.py          # Flask server (dashboard + embedded scheduler)
│   └── templates/index.html
├── render.yaml         # one-click Render Blueprint deploy
├── Procfile            # gunicorn entry point for Render / Heroku-likes
├── samples/            # pre-rendered email + dashboard screenshots
│   ├── email-preview.html / .txt / -subject.txt
│   ├── snapshot.json   # full 565-record real-life scrape
│   └── screenshots/*.png
├── scripts/
│   ├── build_sample.py     # regenerate samples/ from a live scrape
│   └── screenshot_dashboard.py
├── tests/              # offline unit tests for parser/report/storage
├── deploy/
│   └── hexa-connectivity-agent.service
├── .github/workflows/daily-mail.yml
├── Dockerfile
├── docker-compose.yml
├── main.py             # CLI: run / schedule / scrape / test-mail
├── requirements.txt
├── .env.example
└── README.md
```

---

## Running the tests

```bash
pip install pytest
pytest -q
```

The tests are **fully offline** — they exercise the HTML parser, snapshot
diffing, and the Jinja2 renderer without touching the network or SMTP.

---

## Adapting to a different government source

The parser expects a 10-column HTML table:

```
Sr No | Date | Region | State | Substation | Application ID |
Applicant | Type | Installed (MW) | Deemed GNA (MW)
```

If your target page differs (e.g. POSOCO / Grid-India ATC, a different
state utility, or a PDF/Excel publication), update `hexa_agent/scraper.py`:

- Adjust `EXPECTED_COLUMNS` and the field mapping in `_parse_rows`.
- Or replace the whole `scrape_connectivity` function — everything
  downstream (`agent.py`, `report.py`) only depends on the
  `ConnectivityRecord` dataclass and `ScrapeResult` container.

---

## Troubleshooting

| Symptom                                      | Cause / fix                                                                                              |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `MailerError: ... 535, b'5.7.8 ...'`         | Wrong SMTP password — for Gmail, use an **App Password**, not your account password.                     |
| `MailerError: ... STARTTLS extension not supported` | Set `SMTP_USE_TLS=false` or move to port 465.                                                       |
| No email at 23:00                            | Confirm `TIMEZONE` matches your locale; check `journalctl -u hexa-connectivity-agent` or container logs. |
| Scraper returns 0 rows                       | The CTUIL site occasionally changes class names. Run `python main.py --log-level DEBUG scrape` to inspect. |
| Mail flagged as spam                         | Use a verified sender (SES / SendGrid) or set up SPF / DKIM on a custom domain.                          |

---

## License

This project is intended for internal automation use. The CTUIL data
itself is public information published by the Government of India; you
are responsible for using it in accordance with the site's terms.
