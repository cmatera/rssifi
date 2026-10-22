from types import SimpleNamespace

from app.scraper import scrape

from .pages import BLOG, CUSTOM, LIST, STATUS_V1, STATUS_V2


def site(**kw):
    base = dict(url="https://example.com/", mode="auto", item_selector="", title_selector="",
                link_selector="", summary_selector="", content_selector="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_auto_articles():
    r = scrape(site(), html=BLOG)
    assert r.page_title == "Example Blog"
    assert [i.link for i in r.items] == [
        "https://example.com/posts/three",
        "https://example.com/posts/two",
        "https://example.com/posts/one",
    ]
    assert r.items[0].title == "Third post about interesting things"
    assert r.items[0].summary == "Summary three."


def test_auto_link_groups_ignores_nav():
    r = scrape(site(url="https://example.com/news"), html=LIST)
    assert [i.link for i in r.items] == [f"https://example.com/n/{n}" for n in (1, 2, 3, 4)]
    assert "Oct 1" in r.items[0].summary


def test_css_mode():
    r = scrape(site(mode="css", item_selector=".rel", title_selector=".v",
                    link_selector="a.dl", summary_selector=".notes"), html=CUSTOM)
    assert [(i.title, i.link, i.summary) for i in r.items] == [
        ("v2.0", "https://example.com/dl/2", "Big release"),
        ("v1.0", "https://example.com/dl/1", "First"),
    ]


def test_change_mode_hash_changes():
    a = scrape(site(mode="change"), html=STATUS_V1)
    b = scrape(site(mode="change"), html=STATUS_V2)
    assert a.content_hash != b.content_hash
    assert a.items[0].guid != b.items[0].guid
    assert "All systems operational" in a.items[0].summary
