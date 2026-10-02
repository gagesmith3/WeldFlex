"""Where the operator's pages are linked from: the home page's Navigation panel,
the Calibration menu and the Settings menu.

The app is imported with the robot link's start() stubbed out, so nothing here
reaches a controller.
"""

import importlib
import re

import pytest

from robot_service import WeldFlexRobotService


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    return importlib.import_module("app").app.test_client()


def _links(html: str) -> list[str]:
    return re.findall(r'<a\b[^>]*\bhref="([^"]+)"', html)


def test_home_navigation_is_parts_calibration_points_settings(client):
    html = client.get("/operator").get_data(as_text=True)
    nav = html[html.index("home-nav-grid"):]
    assert [link for link in _links(nav) if link.startswith("/operator/")] == [
        "/operator/parts", "/operator/calibration", "/operator/points", "/operator/settings",
    ]


def test_points_moved_out_of_calibration(client):
    links = _links(client.get("/operator/calibration").get_data(as_text=True))
    assert "/operator/points" not in links
    assert {"/operator/jog", "/operator/calibration/force-sensor"} <= set(links)


def test_robot_diagnostics_lives_under_settings(client):
    assert "/operator/settings/diagnostics" in _links(client.get("/operator/settings").get_data(as_text=True))
    assert client.get("/operator/settings/diagnostics").status_code == 200

    old = client.get("/operator/robot-diagnostics")
    assert old.status_code == 302
    assert old.headers["Location"].endswith("/operator/settings/diagnostics")
