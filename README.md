# rssifi

Turn any website into an RSS feed. Add a site in the web UI and rssifi reads the page
on a schedule you choose, then publishes a standard RSS 2.0 feed URL you can paste
into any RSS reader (Feedly, Inoreader, NetNewsWire, Miniflux, FreshRSS…).

## Features

- **Add, edit, pause, refresh and delete sites** from a web UI.
- **Three extraction modes**
  - **Automatic**: detects the list of articles/posts on a page (`<article>` blocks
    or the largest group of similar links). Works for most blogs, news and release pages.
  - **CSS selectors**: you choose the item, title, link and summary selectors.
  - **Page change**: emits a new item whenever the page text (or one selected part of it)
    changes. Good for status pages, prices and "no list" pages.
- **Preview** what a site will produce before saving.
- **Schedules per site**: a fixed interval (15 min … 1 week, or any number of minutes)
  or a cron expression (UTC).
- New items are detected by link/GUID, so readers only see what's actually new.
- **OPML export** to subscribe to every feed at once.
- Optional **basic auth** for the management UI. Feed URLs stay public, so readers can
  fetch them without credentials. Each feed URL contains a random, unguessable ID.
- SQLite storage in a Docker volume.

## Run with Docker

```bash
docker compose up -d --build
```

Open http://localhost:8000, click **Add site**, paste a URL, click **Preview items**
and then **Add site**. Copy the feed URL into your RSS reader.

To use a prebuilt image from GitHub Container Registry (published by CI on every push
to `main`):

```bash
docker run -d --name rssifi -p 8000:8000 -v rssifi-data:/data ghcr.io/<owner>/rssifi:latest
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `RSSIFI_BASE_URL` | request URL | Public URL used in feed links, e.g. `https://rssifi.example.com`. Set this when running behind a reverse proxy or when your RSS reader is on another machine. |
| `RSSIFI_ADMIN_USER` / `RSSIFI_ADMIN_PASSWORD` | unset | Enable HTTP basic auth on the UI. |
| `RSSIFI_MAX_ITEMS` | `50` | Maximum items per feed (and per scrape). |
| `RSSIFI_REQUEST_TIMEOUT` | `20` | Seconds to wait for a site. |
| `RSSIFI_USER_AGENT` | rssifi UA | User-Agent sent to sites. |
| `RSSIFI_DATA_DIR` | `/data` | Where the SQLite database is stored. |

> If your RSS reader is a hosted service (Feedly, Inoreader…), it has to reach rssifi
> over the internet. Expose it through a reverse proxy or tunnel and set `RSSIFI_BASE_URL`.

Run a **single** container/worker: the scheduler runs inside the app process.

## Endpoints

| Path | Purpose |
|---|---|
| `/` | Manage sites |
| `/feeds/<id>.xml` | RSS feed for a site (public) |
| `/opml` | OPML export of all feeds |
| `/healthz` | Health check |

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
RSSIFI_DATA_DIR=./data .venv/bin/uvicorn app.main:app --reload
```

Layout:

```
app/
  main.py       FastAPI routes (UI, feeds, OPML)
  scraper.py    fetch + extraction (auto / css / change)
  service.py    refresh a site and store new items
  scheduler.py  APScheduler jobs, one per site
  feed.py       RSS generation
  db.py         SQLAlchemy models
  templates/    Jinja2 UI
tests/          pytest suite (HTTP mocked with respx)
```

## Limitations

- Pages that render their content with JavaScript only (no server-side HTML) can't be
  read. Try CSS mode against the site's static HTML, or page-change mode.
- Item dates are when rssifi first saw the item, not the site's own publish date.
