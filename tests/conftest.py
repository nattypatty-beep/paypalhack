from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import AppService
from tests.support import make_settings


@pytest.fixture()
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture()
def service(settings):
    service = AppService(settings)
    service.bootstrap()
    try:
        yield service
    finally:
        service.close()


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
