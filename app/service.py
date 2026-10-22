"""Refresh a site: scrape it and store any new items."""

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from .config import settings
from .db import MODE_CHANGE, Item, SessionLocal, Site, utcnow
from .scraper import ScrapeError, scrape

log = logging.getLogger("rssifi")


def refresh_site(site_id: int) -> tuple[int, str]:
    """Scrape the site and persist new items. Returns (new_item_count, error)."""
    with SessionLocal() as db:
        site = db.get(Site, site_id)
        if site is None:
            return 0, "site not found"
        site.last_checked_at = utcnow()
        try:
            result = scrape(site)
        except ScrapeError as e:
            site.last_error = str(e)
            db.commit()
            log.warning("refresh failed for %s: %s", site.url, e)
            return 0, str(e)
        except Exception as e:  # never let one bad site kill the scheduler
            site.last_error = f"Unexpected error: {e}"
            db.commit()
            log.exception("refresh crashed for %s", site.url)
            return 0, site.last_error

        site.last_error = ""
        site.last_success_at = utcnow()

        if site.mode == MODE_CHANGE:
            first_run = not site.content_hash
            changed = result.content_hash != site.content_hash
            site.content_hash = result.content_hash
            # Record a baseline item on first run so the feed is never empty.
            items = result.items if (changed or first_run) else []
        else:
            items = result.items

        existing = set(
            db.scalars(select(Item.guid).where(Item.site_id == site.id)).all()
        )
        new = [it for it in items if it.guid not in existing]
        # Add in reverse page order so the item at the top of the page gets the
        # newest timestamp and sorts first in the feed.
        now = utcnow()
        for offset, it in enumerate(reversed(new)):
            db.add(
                Item(
                    site_id=site.id,
                    guid=it.guid,
                    title=it.title,
                    link=it.link,
                    summary=it.summary,
                    first_seen_at=now + timedelta(milliseconds=offset),
                )
            )
        db.flush()
        _prune(db, site.id)
        db.commit()
        log.info("refreshed %s: %d new item(s)", site.url, len(new))
        return len(new), ""


def _prune(db, site_id: int) -> None:
    """Keep only the newest items (2x the feed size) per site."""
    keep = settings.max_items_per_feed * 2
    ids = db.scalars(
        select(Item.id)
        .where(Item.site_id == site_id)
        .order_by(Item.first_seen_at.desc())
        .offset(keep)
    ).all()
    if ids:
        db.execute(delete(Item).where(Item.id.in_(ids)))
