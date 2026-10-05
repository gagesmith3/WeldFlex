"""Where a part's next live run picks up: written onto the recipe when a run
finishes, cleared when the studs change, and offered by the Load modal.

Same harness as test_app_run_modes: the robot link's start() is stubbed and
recipes.json is redirected to a temp file, so nothing reaches a controller.
"""

import importlib
import json
import re
from types import SimpleNamespace

import pytest

from robot_service import WeldFlexRobotService

STUDS = [{"x": 10, "y": 20}, {"x": 30, "y": 40}, {"x": 50, "y": 60}]


def _recipe(**overrides):
    recipe = {
        "id": "part-1",
        "name": "fabtech polygon",
        "studs": list(STUDS),
        "safe_z": 60.0,
        "retract_z": 30.0,
        "search_z": 10.0,
        "part_z": 0.0,
        "pressure_setting": 16.0,
    }
    recipe.update(overrides)
    return recipe


def _run(**overrides):
    record = {
        "run_id": "run-1",
        "part_id": "part-1",
        "kind": "part",
        "arm_mode": "live",
        "stud_count": 3,
        "cycles_done": 0,
        "cycle_times": [],
        "ended_at": "2026-10-05T14:02:11",
        "last_stud": 2,
        "last_stud_cycle": 1,
        "last_stud_partial": False,
        "next_stud": 3,
        "stud_progress_exact": True,
    }
    record.update(overrides)
    return record


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    path = tmp_path / "recipes.json"
    monkeypatch.setattr(module, "_RECIPES_PATH", str(path))
    return SimpleNamespace(
        module=module,
        client=module.app.test_client(),
        write=lambda recipes: path.write_text(json.dumps(recipes), encoding="utf-8"),
        read=lambda: json.loads(path.read_text(encoding="utf-8")),
    )


def test_a_live_run_records_where_the_next_one_picks_up(app_env):
    app_env.write([_recipe()])
    app_env.module._on_job_finish(_run())
    assert app_env.read()[0]["stud_progress"] == {
        "next_stud": 3, "last_stud": 2, "stud_count": 3, "exact": True,
        "ended_at": "2026-10-05T14:02:11", "run_id": "run-1",
    }


@pytest.mark.parametrize("record", [
    _run(arm_mode="dry"),                  # a dry run puts nothing on the plate
    _run(kind="single_shot"),
    _run(next_stud=None, last_stud=None),  # never launched
    {k: v for k, v in _run().items() if k not in ("next_stud", "last_stud")},  # old record
])
def test_other_runs_leave_the_recorded_progress_alone(app_env, record):
    saved = {"next_stud": 2, "last_stud": 1, "stud_count": 3, "exact": True,
             "ended_at": "2026-10-04T10:00:00", "run_id": "older"}
    app_env.write([_recipe(stud_progress=saved)])
    app_env.module._on_job_finish(record)
    assert app_env.read()[0]["stud_progress"] == saved


def test_an_inexact_reading_is_recorded_as_such(app_env):
    app_env.write([_recipe()])
    app_env.module._on_job_finish(_run(stud_progress_exact=False))
    assert app_env.read()[0]["stud_progress"]["exact"] is False


def test_saving_the_same_studs_keeps_the_progress_and_changing_them_drops_it(app_env):
    saved = {"next_stud": 3, "last_stud": 2, "stud_count": 3, "exact": True,
             "ended_at": "2026-10-05T14:02:11", "run_id": "run-1"}
    app_env.write([_recipe(stud_progress=saved)])
    form = {"recipe_id": "part-1", "recipe_name": "fabtech polygon",
            "safe_z": "60", "retract_z": "30", "search_z": "10"}

    app_env.client.post("/ui/recipes/save", data={**form, "studs_json": json.dumps(STUDS)})
    assert app_env.read()[0]["stud_progress"] == saved

    moved = STUDS[:2] + [{"x": 55, "y": 60}]
    app_env.client.post("/ui/recipes/save", data={**form, "studs_json": json.dumps(moved)})
    assert "stud_progress" not in app_env.read()[0]


def test_the_parts_page_sends_the_progress_and_the_modal_opens_on_it(app_env):
    saved = {"next_stud": 3, "last_stud": 2, "stud_count": 3, "exact": True,
             "ended_at": "2026-10-05T14:02:11", "run_id": "run-1"}
    app_env.write([_recipe(stud_progress=saved)])
    html = app_env.client.get("/operator/parts").get_data(as_text=True)
    data = re.search(r'<script type="application/json" id="parts-data">(.*?)</script>', html, re.S)
    [part] = json.loads(data.group(1))
    assert part["stud_progress"] == saved
    # The modal opens on next_stud when it still matches the part's stud count,
    # and says where the number came from.
    assert "progress.stud_count !== studCount" in html
    assert "String(_runResume ? _runResume.next_stud : 1)" in html
    assert "Last live run: after stud" in html


@pytest.mark.parametrize("url", ["/manager/reports", "/ui/manager/reports"])
def test_the_reports_part_filter_lists_the_parts_however_the_page_is_reached(app_env, url):
    """Opened directly, /manager/reports used to render the filter with no
    parts: only the sidebar's HTMX load passed the part list."""
    app_env.write([_recipe(), _recipe(id="part-2", name="fabtech dummy")])
    html = app_env.client.get(url).get_data(as_text=True)
    select = re.search(r'<select id="mgr-report-part-filter".*?</select>', html, re.S).group(0)
    assert 'value="part-1"' in select and 'value="part-2"' in select
