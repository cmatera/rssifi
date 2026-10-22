"""Fetch a web page and turn it into a list of feed items.

Three extraction modes are supported:

* ``auto``   – heuristically find the repeated block of links that looks like
               a list of articles/posts (works for most blogs and news pages).
* ``css``    – use CSS selectors supplied by the user.
* ``change`` – treat the page as a single document and emit a new item every
               time its text content changes.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .config import settings

NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form")
CHROME_TAGS = ("nav", "header", "footer", "aside")
SKIP_LINK_TEXT = re.compile(
    r"^(home|about|contact|login|log in|sign in|sign up|register|privacy|terms|"
    r"next|previous|prev|more|read more|continue reading|menu|search|skip to .*|\d+)$",
    re.I,
)


@dataclass
class ScrapedItem:
    title: str
    link: str
    summary: str = ""
    guid: str = ""

    def __post_init__(self) -> None:
        if not self.guid:
            self.guid = self.link


@dataclass
class ScrapeResult:
    page_title: str
    items: list[ScrapedItem]
    content_hash: str = ""


class ScrapeError(Exception):
    pass


def fetch(url: str) -> tuple[str, str]:
    """Return (final_url, html)."""
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en;q=0.9,*;q=0.5",
    }
    try:
        with httpx.Client(
            follow_redirects=True, timeout=settings.request_timeout, headers=headers
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ScrapeError(f"HTTP {e.response.status_code} fetching {url}") from e
    except httpx.HTTPError as e:
        raise ScrapeError(f"Error fetching {url}: {e}") from e
    return str(resp.url), resp.text


def scrape(site, html: str | None = None, final_url: str | None = None) -> ScrapeResult:
    """Scrape ``site`` (anything with url/mode/selector attributes)."""
    if html is None:
        final_url, html = fetch(site.url)
    base_url = final_url or site.url
    soup = BeautifulSoup(html, "lxml")
    page_title = _text(soup.title) if soup.title else urlparse(base_url).netloc

    if site.mode == "css":
        items = extract_css(soup, base_url, site)
    elif site.mode == "change":
        return extract_change(soup, base_url, site, page_title)
    else:
        items = extract_auto(soup, base_url)

    return ScrapeResult(page_title=page_title, items=_dedupe(items)[: settings.max_items_per_feed])


# --------------------------------------------------------------------------- helpers


def _text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _abs(base: str, href: str | None) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    return urldefrag(urljoin(base, href))[0]


def _dedupe(items: list[ScrapedItem]) -> list[ScrapedItem]:
    seen: set[str] = set()
    out = []
    for it in items:
        if it.guid in seen or not it.title:
            continue
        seen.add(it.guid)
        out.append(it)
    return out


def _clean(soup: BeautifulSoup, strip_chrome: bool = False) -> None:
    for t in soup(NOISE_TAGS):
        t.decompose()
    if strip_chrome:
        for t in soup(CHROME_TAGS):
            t.decompose()


# --------------------------------------------------------------------------- css mode


def extract_css(soup: BeautifulSoup, base_url: str, site) -> list[ScrapedItem]:
    if not site.item_selector:
        raise ScrapeError("CSS mode requires an item selector")
    try:
        blocks = soup.select(site.item_selector)
    except Exception as e:  # soupsieve raises various errors for bad selectors
        raise ScrapeError(f"Invalid item selector: {e}") from e

    items = []
    for block in blocks:
        title_el = _select_one(block, site.title_selector) or block
        link_el = _select_one(block, site.link_selector)
        if link_el is None:
            link_el = block if block.name == "a" else block.find("a", href=True)
        link = _abs(base_url, link_el.get("href")) if link_el is not None else ""
        summary_el = _select_one(block, site.summary_selector)
        summary = _text(summary_el) if summary_el is not None else ""
        title = _text(title_el)
        if not title:
            continue
        if not link:
            # No link: fall back to page URL and use the title as identity.
            link = base_url
            guid = f"{base_url}#{hashlib.sha1(title.encode()).hexdigest()[:12]}"
        else:
            guid = link
        items.append(ScrapedItem(title=title[:500], link=link, summary=summary[:2000], guid=guid))
    return items


def _select_one(block: Tag, selector: str):
    if not selector:
        return None
    try:
        return block.select_one(selector)
    except Exception as e:
        raise ScrapeError(f"Invalid selector {selector!r}: {e}") from e


# --------------------------------------------------------------------------- change mode


def extract_change(soup: BeautifulSoup, base_url: str, site, page_title: str) -> ScrapeResult:
    root = None
    if site.content_selector:
        try:
            root = soup.select_one(site.content_selector)
        except Exception as e:
            raise ScrapeError(f"Invalid content selector: {e}") from e
        if root is None:
            raise ScrapeError(f"Content selector {site.content_selector!r} matched nothing")
    else:
        _clean(soup, strip_chrome=True)
        root = soup.find("main") or soup.find("article") or soup.body or soup
    for t in root(NOISE_TAGS):
        t.decompose()
    text = _text(root)
    digest = hashlib.sha256(text.encode()).hexdigest()
    snippet = text[:1000] + ("…" if len(text) > 1000 else "")
    item = ScrapedItem(
        title=f"{page_title} updated",
        link=base_url,
        summary=snippet,
        guid=f"{base_url}#{digest[:16]}",
    )
    return ScrapeResult(page_title=page_title, items=[item], content_hash=digest)


# --------------------------------------------------------------------------- auto mode


def extract_auto(soup: BeautifulSoup, base_url: str) -> list[ScrapedItem]:
    _clean(soup)
    items = _from_articles(soup, base_url)
    if len(items) >= 2:
        return items
    for t in soup(CHROME_TAGS):
        t.decompose()
    return _from_link_groups(soup, base_url)


def _from_articles(soup: BeautifulSoup, base_url: str) -> list[ScrapedItem]:
    """Pages that use <article> elements (very common for blogs/news)."""
    items = []
    for art in soup.find_all("article"):
        heading = art.find(["h1", "h2", "h3", "h4"])
        link_el = None
        if heading is not None:
            link_el = heading.find("a", href=True) or heading.find_parent("a", href=True)
        if link_el is None:
            link_el = _best_link(art)
        if link_el is None:
            continue
        title = _text(heading) if heading is not None else _text(link_el)
        link = _abs(base_url, link_el.get("href"))
        if not title or not link:
            continue
        items.append(ScrapedItem(title=title[:500], link=link, summary=_summary(art, title)))
    return items


def _best_link(block: Tag):
    links = [a for a in block.find_all("a", href=True) if _text(a)]
    return max(links, key=lambda a: len(_text(a)), default=None)


def _summary(block: Tag, title: str) -> str:
    p = block.find("p")
    text = _text(p) if p is not None else ""
    if not text or text == title:
        text = _text(block).replace(title, "", 1).strip()
    return text[:500]


def _path_signature(a: Tag) -> str:
    """Structural signature of a link: ancestor tag names + classes, ignoring indices."""
    parts = []
    node = a
    for _ in range(6):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        cls = ".".join(sorted(c for c in node.get("class", []) if not re.search(r"\d", c)))
        parts.append(f"{node.name}.{cls}" if cls else node.name)
    return ">".join(reversed(parts))


def _from_link_groups(soup: BeautifulSoup, base_url: str) -> list[ScrapedItem]:
    """Group links by structural position and pick the group that looks like a list of posts."""
    host = urlparse(base_url).netloc
    groups: dict[str, list[Tag]] = defaultdict(list)
    for a in soup.find_all("a", href=True):
        text = _text(a)
        href = _abs(base_url, a["href"])
        if not href or len(text) < 12 or SKIP_LINK_TEXT.match(text):
            continue
        if href.rstrip("/") == base_url.rstrip("/"):
            continue
        groups[_path_signature(a)].append(a)

    def score(links: list[Tag]) -> float:
        if len(links) < 3:
            return 0
        texts = [_text(a) for a in links]
        avg_len = sum(len(t) for t in texts) / len(texts)
        words = sum(len(t.split()) for t in texts) / len(texts)
        same_host = sum(urlparse(_abs(base_url, a["href"])).netloc == host for a in links) / len(links)
        # Favour many links with sentence-like text that stay on the same site.
        return len(links) * min(words, 12) * (0.5 + same_host) * (1 if avg_len < 300 else 0.3)

    if not groups:
        return []
    best = max(groups.values(), key=score)
    if score(best) == 0:
        return []
    items = []
    for a in best:
        container = a.find_parent(["li", "div", "section", "tr"]) or a.parent
        title = _text(a)
        summary = _text(container).replace(title, "", 1).strip() if container is not None else ""
        items.append(ScrapedItem(title=title[:500], link=_abs(base_url, a["href"]), summary=summary[:500]))
    return items
