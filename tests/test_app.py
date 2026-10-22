import xml.etree.ElementTree as ET

import httpx
import respx

from app.db import SessionLocal, Site

from .pages import BLOG, STATUS_V1, STATUS_V2

FORM = {
    "url": "https://blog.example.com/",
    "name": "Example",
    "mode": "auto",
    "schedule_type": "interval",
    "interval_minutes": "30",
    "cron": "",
    "enabled": "on",
}


def _site():
    with SessionLocal() as db:
        return db.query(Site).one()


@respx.mock
def test_add_site_creates_feed(client):
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(200, text=BLOG))
    r = client.post("/sites", data=FORM, follow_redirects=False)
    assert r.status_code == 303
    site = _site()
    assert site.interval_minutes == 30 and site.last_error == ""

    r = client.get(f"/feeds/{site.slug}.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(r.content)
    titles = [e.text for e in root.iter("title")]
    assert titles[0] == "Example"
    assert root.find("channel/link").text == "https://blog.example.com/"
    assert titles[1:] == [
        "Third post about interesting things",
        "Second post about other things",
        "First post ever on this blog",
    ]

    assert "Example" in client.get("/").text
    assert "Third post" in client.get(f"/sites/{site.id}").text


@respx.mock
def test_refresh_only_adds_new_items(client):
    route = respx.get("https://blog.example.com/").mock(return_value=httpx.Response(200, text=BLOG))
    client.post("/sites", data=FORM)
    site = _site()
    newer = BLOG.replace("<main>", '<main><article><h2><a href="/posts/four">Fourth post just published</a></h2></article>')
    route.mock(return_value=httpx.Response(200, text=newer))
    client.post(f"/sites/{site.id}/refresh")
    with SessionLocal() as db:
        items = db.get(Site, site.id).items
        assert len(items) == 4
        assert items[0].title == "Fourth post just published"


@respx.mock
def test_change_mode(client):
    route = respx.get("https://status.example.com/").mock(return_value=httpx.Response(200, text=STATUS_V1))
    client.post("/sites", data={**FORM, "url": "https://status.example.com/", "mode": "change"})
    site = _site()
    client.post(f"/sites/{site.id}/refresh")  # unchanged -> no new item
    route.mock(return_value=httpx.Response(200, text=STATUS_V2))
    client.post(f"/sites/{site.id}/refresh")
    with SessionLocal() as db:
        items = db.get(Site, site.id).items
        assert len(items) == 2
        assert "Degraded" in items[0].summary


@respx.mock
def test_fetch_error_is_recorded(client):
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(500))
    client.post("/sites", data=FORM)
    assert "HTTP 500" in _site().last_error


def test_validation_errors(client):
    r = client.post("/sites", data={**FORM, "url": "ftp://x", "schedule_type": "cron", "cron": "bad"})
    assert r.status_code == 200
    assert "URL must start with" in r.text and "Invalid cron" in r.text


@respx.mock
def test_edit_and_delete(client):
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(200, text=BLOG))
    client.post("/sites", data=FORM)
    site = _site()
    client.post(f"/sites/{site.id}", data={**FORM, "name": "Renamed", "schedule_type": "cron", "cron": "0 7 * * *"})
    s = _site()
    assert s.name == "Renamed" and s.schedule_type == "cron" and s.cron == "0 7 * * *"
    assert client.get(f"/sites/{site.id}/edit").status_code == 200
    client.post(f"/sites/{site.id}/delete")
    assert client.get(f"/feeds/{site.slug}.xml").status_code == 404


@respx.mock
def test_preview(client):
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(200, text=BLOG))
    r = client.post("/preview", data=FORM)
    assert "3 item(s) found" in r.text


@respx.mock
def test_opml(client):
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(200, text=BLOG))
    client.post("/sites", data=FORM)
    r = client.get("/opml")
    assert 'xmlUrl="http://testserver/feeds/' in r.text
