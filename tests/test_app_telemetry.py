import importlib
import json
import threading
import time

import pytest

from robot_feed import FeedSnapshot
from robot_service import WeldFlexRobotService


@pytest.fixture
def telemetry_app(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    monkeypatch.setattr(module, "_force_stream_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(
        module.robot, "_call",
        lambda *args, **kwargs: pytest.fail("readout must never issue robot RPC"),
    )
    return module


def test_force_stream_sends_cached_reading_and_releases_slot(telemetry_app, monkeypatch):
    monkeypatch.setattr(telemetry_app.robot, "feed_snapshot", lambda: FeedSnapshot(
        fields={"ft_data": [0, 0, -10, 0, 0, 0], "ft_act_status": 1},
        received_monotonic=time.monotonic(),
    ))
    client = telemetry_app.app.test_client()
    response = client.get("/ui/ft/stream", buffered=False)
    try:
        assert response.mimetype == "text/event-stream"
        assert next(response.response) == b"retry: 2000\n\n"
        event = next(response.response).decode()
        payload = json.loads(event.removeprefix("data: "))
        assert "+2.2" in payload["html"]
        assert "live 8083" in payload["html"]
        assert client.get("/ui/ft/stream").status_code == 429
    finally:
        response.close()
    assert telemetry_app._force_stream_slots.acquire(blocking=False)


def test_force_reading_renders_stale_without_rpc(telemetry_app):
    response = telemetry_app.app.test_client().get("/ui/ft/reading")
    assert response.status_code == 200
    assert b"Force data is unavailable or stale" in response.data
    assert b"ft-reading-stale" in response.data