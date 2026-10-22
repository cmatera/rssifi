import os
import tempfile

# Configure before the app is imported.
_tmp = tempfile.mkdtemp(prefix="rssifi-test-")
os.environ["RSSIFI_DATA_DIR"] = _tmp
os.environ["RSSIFI_RUN_SCHEDULER"] = "0"
os.environ.pop("RSSIFI_ADMIN_USER", None)
os.environ.pop("RSSIFI_ADMIN_PASSWORD", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
