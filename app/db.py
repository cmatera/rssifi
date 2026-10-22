import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_slug() -> str:
    return secrets.token_urlsafe(9)


class Base(DeclarativeBase):
    pass


# Extraction modes
MODE_AUTO = "auto"  # detect repeated article/link blocks automatically
MODE_CSS = "css"  # user-supplied CSS selectors
MODE_CHANGE = "change"  # one item each time the page content changes
MODES = (MODE_AUTO, MODE_CSS, MODE_CHANGE)

# Schedule types
SCHEDULE_INTERVAL = "interval"
SCHEDULE_CRON = "cron"


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, default=new_slug)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2000))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    mode: Mapped[str] = mapped_column(String(16), default=MODE_AUTO)
    item_selector: Mapped[str] = mapped_column(String(500), default="")
    title_selector: Mapped[str] = mapped_column(String(500), default="")
    link_selector: Mapped[str] = mapped_column(String(500), default="")
    summary_selector: Mapped[str] = mapped_column(String(500), default="")
    content_selector: Mapped[str] = mapped_column(String(500), default="")

    schedule_type: Mapped[str] = mapped_column(String(16), default=SCHEDULE_INTERVAL)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    cron: Mapped[str] = mapped_column(String(100), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")

    items: Mapped[list["Item"]] = relationship(
        back_populates="site",
        cascade="all, delete-orphan",
        order_by="Item.first_seen_at.desc()",
    )

    @property
    def schedule_label(self) -> str:
        if self.schedule_type == SCHEDULE_CRON:
            return f"cron: {self.cron}"
        m = self.interval_minutes
        for size, unit in ((10080, "week"), (1440, "day"), (60, "hour"), (1, "minute")):
            if m % size == 0:
                n = m // size
                return f"every {unit}" if n == 1 else f"every {n} {unit}s"
        return f"every {m} minutes"


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("site_id", "guid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(1000))
    link: Mapped[str] = mapped_column(String(2000))
    summary: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    site: Mapped[Site] = relationship(back_populates="items")


def _make_engine(url: str):
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    eng = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return eng


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
