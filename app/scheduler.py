"""Background scheduler: one APScheduler job per enabled site."""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from .db import SCHEDULE_CRON, SessionLocal, Site
from .service import refresh_site

log = logging.getLogger("rssifi")

scheduler = BackgroundScheduler(
    timezone="UTC",
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
)


def job_id(site_id: int) -> str:
    return f"site-{site_id}"


def make_trigger(site: Site):
    if site.schedule_type == SCHEDULE_CRON:
        return CronTrigger.from_crontab(site.cron, timezone="UTC")
    return IntervalTrigger(minutes=max(1, site.interval_minutes), timezone="UTC")


def validate_cron(expr: str) -> str | None:
    try:
        CronTrigger.from_crontab(expr, timezone="UTC")
    except Exception as e:
        return str(e)
    return None


def schedule_site(site: Site) -> None:
    unschedule_site(site.id)
    if not site.enabled:
        return
    trigger = make_trigger(site)
    kwargs = {}
    if site.schedule_type != SCHEDULE_CRON:
        # Run soon if the site is overdue, otherwise wait for the next interval.
        last = site.last_checked_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        due = (last or now) + timedelta(minutes=max(1, site.interval_minutes))
        kwargs["next_run_time"] = max(due, now + timedelta(seconds=5)) if last else now + timedelta(seconds=5)
    scheduler.add_job(
        refresh_site, trigger, args=[site.id], id=job_id(site.id), replace_existing=True, **kwargs
    )


def unschedule_site(site_id: int) -> None:
    if scheduler.get_job(job_id(site_id)):
        scheduler.remove_job(job_id(site_id))


def next_run(site_id: int) -> datetime | None:
    job = scheduler.get_job(job_id(site_id))
    return getattr(job, "next_run_time", None) if job else None


def start() -> None:
    with SessionLocal() as db:
        for site in db.scalars(select(Site)).all():
            schedule_site(site)
    scheduler.start()
    log.info("scheduler started with %d job(s)", len(scheduler.get_jobs()))


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
