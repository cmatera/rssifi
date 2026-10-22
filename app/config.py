import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("RSSIFI_DATA_DIR", "./data")
    # Public base URL used in feed links, e.g. https://rssifi.example.com.
    # When unset, the URL of the incoming request is used.
    base_url: str | None = os.getenv("RSSIFI_BASE_URL") or None
    # Optional HTTP basic auth for the management UI. Feeds stay public so
    # RSS readers can fetch them without credentials.
    admin_user: str | None = os.getenv("RSSIFI_ADMIN_USER") or None
    admin_password: str | None = os.getenv("RSSIFI_ADMIN_PASSWORD") or None
    user_agent: str = os.getenv(
        "RSSIFI_USER_AGENT",
        "Mozilla/5.0 (compatible; rssifi/0.1)",
    )
    request_timeout: float = float(os.getenv("RSSIFI_REQUEST_TIMEOUT", "20"))
    max_items_per_feed: int = int(os.getenv("RSSIFI_MAX_ITEMS", "50"))
    run_scheduler: bool = os.getenv("RSSIFI_RUN_SCHEDULER", "1") != "0"

    @property
    def database_url(self) -> str:
        return os.getenv(
            "RSSIFI_DATABASE_URL", f"sqlite:///{os.path.join(self.data_dir, 'rssifi.db')}"
        )


settings = Settings()
