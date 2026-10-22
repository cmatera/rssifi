import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from xml.sax.saxutils import quoteattr

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from . import __version__, scheduler
from .config import settings
from .db import MODES, SCHEDULE_CRON, SCHEDULE_INTERVAL, SessionLocal, Site, init_db
from .feed import build_rss
from .scraper import ScrapeError, scrape
from .service import refresh_site

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.run_scheduler:
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="rssifi", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _allowed_hosts(request: Request) -> set[str]:
    hosts = {request.headers.get("host", "").lower()}
    if fwd := request.headers.get("x-forwarded-host"):
        hosts.add(fwd.split(",")[0].strip().lower())
    if settings.base_url:
        hosts.add(urlsplit(settings.base_url).netloc.lower())
    hosts.discard("")
    return hosts


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Reject cross-site form submissions.

    Browsers resend basic-auth credentials automatically, so a hostile page could
    otherwise submit forms to rssifi on the user's behalf. Browsers always attach
    Origin (or at least Referer) to cross-site POSTs; requests with neither come
    from non-browser clients and are allowed.
    """
    if request.method in UNSAFE_METHODS:
        source = request.headers.get("origin") or request.headers.get("referer")
        if source is not None:
            host = urlsplit(source).netloc.lower() if source != "null" else ""
            if host not in _allowed_hosts(request):
                return PlainTextResponse("Cross-site request rejected", status_code=403)
    return await call_next(request)

_basic = HTTPBasic(auto_error=False)


def require_admin(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not (settings.admin_user and settings.admin_password):
        return
    ok = creds is not None and (
        secrets.compare_digest(creds.username.encode(), settings.admin_user.encode())
        and secrets.compare_digest(creds.password.encode(), settings.admin_password.encode())
    )
    if not ok:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})


def base_url(request: Request) -> str:
    return (settings.base_url or str(request.base_url)).rstrip("/")


def feed_url(request: Request, site: Site) -> str:
    return f"{base_url(request)}/feeds/{site.slug}.xml"


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("feed_url", lambda s: feed_url(request, s))
    ctx.setdefault("next_run", scheduler.next_run)
    return templates.TemplateResponse(request, name, ctx)


# --------------------------------------------------------------------------- form handling

FORM_FIELDS = (
    "name", "url", "description", "mode", "item_selector", "title_selector",
    "link_selector", "summary_selector", "content_selector", "schedule_type",
    "interval_minutes", "cron",
)


def parse_form(form) -> tuple[dict, list[str]]:
    data = {f: (form.get(f) or "").strip() for f in FORM_FIELDS}
    errors = []
    if not data["url"].startswith(("http://", "https://")):
        errors.append("URL must start with http:// or https://")
    if data["mode"] not in MODES:
        errors.append("Unknown extraction mode")
    if data["mode"] == "css" and not data["item_selector"]:
        errors.append("CSS mode needs an item selector")
    if data["schedule_type"] not in (SCHEDULE_INTERVAL, SCHEDULE_CRON):
        errors.append("Unknown schedule type")
    try:
        data["interval_minutes"] = int(data["interval_minutes"] or 60)
        if data["interval_minutes"] < 1:
            raise ValueError
    except ValueError:
        errors.append("Interval must be a whole number of minutes (1 or more)")
        data["interval_minutes"] = 60
    if data["schedule_type"] == SCHEDULE_CRON:
        if err := scheduler.validate_cron(data["cron"]):
            errors.append(f"Invalid cron expression: {err}")
    if not data["name"]:
        data["name"] = data["url"].split("//", 1)[-1].split("/", 1)[0] or "Untitled"
    data["enabled"] = form.get("enabled") is not None
    return data, errors


# --------------------------------------------------------------------------- UI routes


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def index(request: Request):
    with SessionLocal() as db:
        sites = db.scalars(select(Site).order_by(Site.name)).all()
        counts = {s.id: len(s.items) for s in sites}
    return render(request, "index.html", sites=sites, counts=counts)


@app.get("/sites/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def new_site(request: Request, url: str = ""):
    site = SimpleNamespace(
        id=None, name="", url=url, description="", mode="auto", item_selector="",
        title_selector="", link_selector="", summary_selector="", content_selector="",
        schedule_type="interval", interval_minutes=60, cron="0 * * * *", enabled=True,
    )
    return render(request, "form.html", site=site, errors=[])


@app.post("/sites", dependencies=[Depends(require_admin)])
async def create_site(request: Request):
    data, errors = parse_form(await request.form())
    if errors:
        return render(request, "form.html", site=SimpleNamespace(id=None, **data), errors=errors)
    with SessionLocal() as db:
        site = Site(**data)
        db.add(site)
        db.commit()
        site_id = site.id
    # Populate the feed straight away, then hand it to the scheduler.
    refresh_site(site_id)
    with SessionLocal() as db:
        scheduler.schedule_site(db.get(Site, site_id))
    return RedirectResponse(f"/sites/{site_id}", status_code=303)


def _get_site(db, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "Site not found")
    return site


@app.get("/sites/{site_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def site_detail(request: Request, site_id: int):
    with SessionLocal() as db:
        site = _get_site(db, site_id)
        items = list(site.items)
    return render(request, "site.html", site=site, items=items)


@app.get("/sites/{site_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def edit_site(request: Request, site_id: int):
    with SessionLocal() as db:
        site = _get_site(db, site_id)
    return render(request, "form.html", site=site, errors=[])


@app.post("/sites/{site_id}", dependencies=[Depends(require_admin)])
async def update_site(request: Request, site_id: int):
    data, errors = parse_form(await request.form())
    if errors:
        return render(request, "form.html", site=SimpleNamespace(id=site_id, **data), errors=errors)
    with SessionLocal() as db:
        site = _get_site(db, site_id)
        extraction_changed = any(
            getattr(site, f) != data[f]
            for f in ("url", "mode", "item_selector", "title_selector", "link_selector",
                      "summary_selector", "content_selector")
        )
        for k, v in data.items():
            setattr(site, k, v)
        if extraction_changed:
            site.content_hash = ""
        db.commit()
        scheduler.schedule_site(site)
    if extraction_changed:
        refresh_site(site_id)
    return RedirectResponse(f"/sites/{site_id}", status_code=303)


@app.post("/sites/{site_id}/delete", dependencies=[Depends(require_admin)])
def delete_site(site_id: int):
    with SessionLocal() as db:
        site = _get_site(db, site_id)
        db.delete(site)
        db.commit()
    scheduler.unschedule_site(site_id)
    return RedirectResponse("/", status_code=303)


@app.post("/sites/{site_id}/refresh", dependencies=[Depends(require_admin)])
def refresh(site_id: int):
    with SessionLocal() as db:
        _get_site(db, site_id)
    refresh_site(site_id)
    return RedirectResponse(f"/sites/{site_id}", status_code=303)


@app.post("/sites/{site_id}/toggle", dependencies=[Depends(require_admin)])
def toggle(site_id: int):
    with SessionLocal() as db:
        site = _get_site(db, site_id)
        site.enabled = not site.enabled
        db.commit()
        scheduler.schedule_site(site)
    return RedirectResponse("/", status_code=303)


@app.post("/preview", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def preview(request: Request):
    data, errors = parse_form(await request.form())
    errors = [e for e in errors if "cron" not in e.lower() and "interval" not in e.lower()]
    result, error = None, "; ".join(errors)
    if not error:
        try:
            result = await run_in_threadpool(scrape, SimpleNamespace(**data))
        except ScrapeError as e:
            error = str(e)
    return render(request, "_preview.html", result=result, error=error)


# --------------------------------------------------------------------------- feeds


@app.get("/feeds/{slug}.xml")
def feed(request: Request, slug: str):
    with SessionLocal() as db:
        site = db.scalars(select(Site).where(Site.slug == slug)).first()
        if site is None:
            raise HTTPException(404, "Feed not found")
        body = build_rss(site, feed_url(request, site))
    return Response(body, media_type="application/rss+xml; charset=utf-8")


@app.get("/opml", dependencies=[Depends(require_admin)])
def opml(request: Request):
    with SessionLocal() as db:
        sites = db.scalars(select(Site).order_by(Site.name)).all()
        outlines = "\n".join(
            f'    <outline type="rss" text={quoteattr(s.name)} title={quoteattr(s.name)} '
            f"xmlUrl={quoteattr(feed_url(request, s))} htmlUrl={quoteattr(s.url)}/>"
            for s in sites
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n'
        "  <head><title>rssifi feeds</title></head>\n  <body>\n"
        f"{outlines}\n  </body>\n</opml>\n"
    )
    return Response(
        body,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="rssifi.opml"'},
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}
