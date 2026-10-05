"""Run-mode plumbing at the route layer: what the operator picks for each run,
what the recipe carries, and the recipe migration off the welder profile.

The app is imported with the robot link's start() stubbed out, recipes.json is
redirected to a temp file, and `job` is swapped for a recorder, so nothing here
reaches a controller or the real part library.
"""

import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from robot_service import UniversalRobotState, WeldFlexRobotService


class RecordingJob:
    """Stands in for JobManager: records every load and start, runs nothing."""

    def __init__(self):
        self.loads = []
        self.starts = 0

    def load(self, *args, **kwargs):
        self.loads.append((args, kwargs))

    def start(self):
        self.starts += 1

    def snapshot(self):
        return SimpleNamespace(active=False)


def _recipe(**overrides):
    recipe = {
        "id": "part-1",
        "name": "Bracket",
        "studs": [{"x": 10, "y": 20}],
        "safe_z": 60.0,
        "retract_z": 10.0,
        "part_z": 0.0,
        "pressure_setting": 20.0,
    }
    recipe.update(overrides)
    return recipe


@pytest.fixture
def run_app(monkeypatch, tmp_path):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    path = tmp_path / "recipes.json"
    monkeypatch.setattr(module, "_RECIPES_PATH", str(path))
    job = RecordingJob()
    monkeypatch.setattr(module, "job", job)
    return SimpleNamespace(
        module=module,
        client=module.app.test_client(),
        job=job,
        write=lambda recipes: path.write_text(json.dumps(recipes), encoding="utf-8"),
        read=lambda: json.loads(path.read_text(encoding="utf-8")),
    )


def test_job_load_refuses_a_run_without_live_or_dry(run_app):
    run_app.write([_recipe()])
    for form in (
        {"recipe_id": "part-1", "cycles": "2"},
        {"recipe_id": "part-1", "cycles": "2", "arm_mode": "armed"},
    ):
        response = run_app.client.post("/ui/job/load", data=form)
        assert response.status_code == 400
        assert "Live or Dry" in response.get_json()["error"]
    assert run_app.job.loads == []


@pytest.mark.parametrize("arm_mode", ["live", "dry"])
@pytest.mark.parametrize("saved_di_check", [True, False])
def test_job_load_takes_arm_mode_from_the_run_and_always_checks_di_on_a_part(run_app, arm_mode, saved_di_check):
    """The per-part DI check switch is gone (2026-10-02): a part saved with it
    off before then still loads with the checks on."""
    run_app.write([_recipe(di_check=saved_di_check)])
    response = run_app.client.post(
        "/ui/job/load", data={"recipe_id": "part-1", "cycles": "3", "arm_mode": arm_mode}
    )
    assert response.get_json() == {"ok": True}
    (args, kwargs), = run_app.job.loads
    assert args[0] == "part-1" and args[1] == "Bracket" and args[3] == 3
    assert kwargs["arm_mode"] == arm_mode
    assert kwargs["di_check"] is True
    assert "welder_profile" not in kwargs


def test_a_part_without_a_saved_di_check_defaults_to_checking(run_app):
    run_app.write([_recipe()])
    run_app.client.post("/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "live"})
    (_, kwargs), = run_app.job.loads
    assert kwargs["di_check"] is True


def test_job_load_ignores_a_legacy_saved_arm_mode(run_app):
    """allentown carried a hidden "arm_mode": "dry" that made every run of it dry
    with nothing on screen saying so. The run's own choice is the only source."""
    run_app.write([_recipe(arm_mode="dry")])
    run_app.client.post("/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "live"})
    (_, kwargs), = run_app.job.loads
    assert kwargs["arm_mode"] == "live"
    assert "arm_mode" not in run_app.read()[0]


def test_job_load_passes_the_starting_stud_through_untouched(run_app):
    """The route does not parse it: JobManager.load() knows the stud count and
    refuses a bad one, where a route-side fallback would quietly start at 1."""
    run_app.write([_recipe()])
    run_app.client.post("/ui/job/load",
                        data={"recipe_id": "part-1", "arm_mode": "dry", "start_stud": "7"})
    run_app.client.post("/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "dry"})
    (_, resumed), (_, plain) = run_app.job.loads
    assert resumed["start_stud"] == "7"
    assert plain["start_stud"] is None


def test_job_load_reports_a_refused_starting_stud(run_app, monkeypatch):
    run_app.write([_recipe()])

    def refuse(*args, **kwargs):
        raise run_app.module.JobError("Starting stud 7 is past the part's last stud (1)")

    monkeypatch.setattr(run_app.job, "load", refuse)
    response = run_app.client.post(
        "/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "dry", "start_stud": "7"}
    )
    assert response.status_code == 409
    assert response.get_json() == {
        "ok": False, "error": "Starting stud 7 is past the part's last stud (1)"
    }


def test_the_run_modal_asks_for_a_starting_stud_that_defaults_to_the_first(run_app):
    """Studs count from 1, so the default is 1, not the 0 it first shipped with
    (owner, 2026-09-30). Typed on the kiosk's number pad, so it needs data-kbd
    and must be a text field: the pad writes .value directly. Run buttons carry
    the stud count the modal checks the number against."""
    run_app.write([_recipe(studs=[{"x": 10, "y": 20}, {"x": 30, "y": 40}, {"x": 50, "y": 60}])])
    html = run_app.client.get("/operator/parts").get_data(as_text=True)
    field = re.search(r'<input[^>]*\bid="run-modal-start-stud"[^>]*>', html)
    assert field, "the run modal has no Starting Stud field"
    for attribute in ('type="text"', 'name="start_stud"', 'value="1"', 'data-kbd="num"'):
        assert attribute in field.group(0)
    # Reopening the modal puts it back to 1 unless the part's last live run
    # recorded where to pick up (tests/test_app_stud_progress.py).
    assert "getElementById('run-modal-start-stud').value = String(_runResume ? _runResume.next_stud : 1)" in html
    assert re.search(r'<button[^>]*\bjs-table-run\b[^>]*\bdata-stud-count="3"', html)
    assert "fd.append('start_stud', startStud)" in html


def test_the_parts_list_loads_and_opens_each_parts_details(run_app):
    """The row button queues the job, so it says Load. The name opens the
    read-only details, drawn from data sent with the page."""
    run_app.write([
        _recipe(voltage=120, origin_corner="back_right", studs=[{"x": 1, "y": 2}, {"x": 3, "y": 4}]),
        _recipe(id="shot", name="Single Shot", system="single_shot"),
    ])
    html = run_app.client.get("/operator/parts").get_data(as_text=True)
    assert re.search(r'<button[^>]*\bjs-table-run\b[^>]*>Load</button>', html)
    assert re.search(r'<button[^>]*\bjs-part-detail\b[^>]*\bdata-recipe-id="part-1"', html)
    assert 'id="part-detail-modal"' in html and "bed_map.js" in html

    data = re.search(r'<script type="application/json" id="parts-data">(.*?)</script>', html, re.S)
    parts = json.loads(data.group(1))
    assert [part["id"] for part in parts] == ["part-1"]  # the Single Shot record stays hidden
    assert parts[0]["studs"] == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    assert (parts[0]["voltage"], parts[0]["origin_corner"], parts[0]["studs_count"]) == (120, "back_right", 2)


def test_the_job_panel_says_when_a_run_is_resumed(run_app):
    """On the Cycles card, not beside LIVE and DI OFF: a third tag in that row
    is clipped to an ellipsis on the kiosk, and a resume nobody can see is the
    one thing this tag exists to prevent."""
    from job_manager import JobSnapshot

    queued = dict(state="queued", run_id="r1", part_name="Bracket", cycles_target=1,
                  arm_mode="live", di_check=False)
    with run_app.module.app.test_request_context("/operator"):
        resumed = run_app.module._job_panel(JobSnapshot(**queued, start_stud=12))
        plain = run_app.module._job_panel(JobSnapshot(**queued))
    assert "from stud" not in plain
    tag = resumed.index("from stud 12")
    assert resumed.index("Cycles") < tag < resumed.index("Force Sensor")
    assert resumed.index("DI off") < resumed.index("Cycles")


def test_job_load_refuses_the_single_shot_record(run_app):
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot")])
    response = run_app.client.post("/ui/job/load", data={"recipe_id": "shot", "arm_mode": "dry"})
    assert response.status_code == 404
    assert run_app.job.loads == []


def test_recipes_migrate_off_the_welder_profile_and_the_name_keyed_faceplate(run_app):
    run_app.write([
        _recipe(id="a", name="atlas-part", welder_profile="atlas"),
        _recipe(id="l", name="liberty-part", welder_profile="liberty"),
        _recipe(id="f", name="faceplates", studs=[{"x": 1, "y": 2}]),
    ])
    with run_app.module._rec_lock:
        recipes = run_app.module._recipes_load()

    by_id = {recipe["id"]: recipe for recipe in recipes}
    # Liberty ran with DI off; since the per-part switch went, every part checks.
    assert by_id["a"]["di_check"] is True
    assert by_id["l"]["di_check"] is True
    assert by_id["f"]["system"] == "single_shot"
    assert all("welder_profile" not in recipe for recipe in run_app.read())
    assert [r["id"] for r in run_app.module._hide_system_recipes(recipes)] == ["a", "l"]


def test_recipe_load_turns_the_di_check_back_on_for_every_part_but_single_shot(run_app):
    run_app.write([
        _recipe(id="off", name="Off", di_check=False),
        _recipe(id="unset", name="Unset"),
        _recipe(id="shot", name="Single Shot", system="single_shot", di_check=False),
    ])
    with run_app.module._rec_lock:
        run_app.module._recipes_load()
    by_id = {recipe["id"]: recipe for recipe in run_app.read()}
    assert by_id["off"]["di_check"] is True
    assert by_id["unset"]["di_check"] is True
    assert by_id["shot"]["di_check"] is False


def test_part_save_ignores_a_di_check_off(run_app):
    run_app.write([_recipe(di_check=True)])
    form = {"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20"}
    run_app.client.post("/ui/recipes/save", data={**form, "di_check": "0"})
    assert run_app.read()[0]["di_check"] is True

    run_app.client.post("/ui/recipes/save", data={"recipe_name": "New part", "studs_text": "1,2",
                                                  "di_check": "0"})
    assert next(r for r in run_app.read() if r["name"] == "New part")["di_check"] is True


def test_single_shot_save_sets_di_check_and_keeps_it_when_the_form_omits_it(run_app):
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot", di_check=True)])
    form = {"recipe_id": "shot", "recipe_name": "Single Shot", "target_x": "10", "target_y": "20"}
    run_app.client.post("/ui/recipes/save", data={**form, "di_check": "0"})
    assert run_app.read()[0]["di_check"] is False

    # A form without the field must not quietly turn the checks back on.
    run_app.client.post("/ui/recipes/save", data=form)
    assert run_app.read()[0]["di_check"] is False


def test_recipe_save_sets_the_voltage_and_keeps_it_when_the_form_omits_it(run_app):
    run_app.write([_recipe()])
    form = {"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20"}
    run_app.client.post("/ui/recipes/save", data={**form, "voltage": "120"})
    assert run_app.read()[0]["voltage"] == 120

    # Only the part designer sends it; the operator Parts editor must not clear it.
    run_app.client.post("/ui/recipes/save", data=form)
    assert run_app.read()[0]["voltage"] == 120

    # Blank is "not set".
    run_app.client.post("/ui/recipes/save", data={**form, "voltage": ""})
    assert run_app.read()[0]["voltage"] is None


@pytest.mark.parametrize("voltage", ["0", "201", "12.5", "abc", "-5"])
def test_recipe_save_refuses_a_voltage_out_of_range(run_app, voltage):
    run_app.write([_recipe(voltage=120)])
    response = run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20", "voltage": voltage,
    })
    assert "Voltage must be a whole number" in response.get_data(as_text=True)
    assert run_app.read()[0]["voltage"] == 120


def test_a_new_part_starts_with_no_voltage(run_app):
    run_app.write([])
    run_app.client.post("/ui/recipes/save", data={"recipe_name": "Fresh", "studs_text": "1,2"})
    assert run_app.read()[0]["voltage"] is None


def test_recipe_save_sets_the_origin_corner_and_keeps_it_when_the_form_omits_it(run_app):
    run_app.write([_recipe()])
    form = {"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20"}
    run_app.client.post("/ui/recipes/save", data={**form, "origin_corner": "back_right"})
    assert run_app.read()[0]["origin_corner"] == "back_right"

    # Only the part designer sends it. The operator Parts editor's save must not
    # quietly move the part's studs back to front-left.
    run_app.client.post("/ui/recipes/save", data=form)
    assert run_app.read()[0]["origin_corner"] == "back_right"


def test_recipe_save_refuses_an_unknown_origin_corner(run_app):
    run_app.write([_recipe(origin_corner="back_left")])
    response = run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20",
        "origin_corner": "top_right",
    })
    assert "Unknown origin corner" in response.get_data(as_text=True)
    assert "X-Recipe-Id" not in response.headers
    assert run_app.read()[0]["origin_corner"] == "back_left"


def test_new_and_legacy_parts_are_measured_from_zerozero(run_app):
    run_app.write([_recipe()])
    run_app.client.post("/ui/recipes/save", data={"recipe_name": "New", "studs_text": "1,2"})
    assert next(r for r in run_app.read() if r["name"] == "New")["origin_corner"] == "front_left"
    with run_app.module._rec_lock:
        enriched = run_app.module._recipes_enrich(run_app.module._recipes_load())
    assert next(r for r in enriched if r["id"] == "part-1")["origin_corner"] == "front_left"


def test_job_load_takes_the_origin_corner_from_the_part(run_app):
    run_app.write([_recipe(origin_corner="front_right")])
    run_app.client.post("/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "dry"})
    (_, kwargs), = run_app.job.loads
    assert kwargs["origin_corner"] == "front_right"


def test_recipes_migrate_to_three_heights_without_changing_how_they_travel(run_app):
    """Until 2026-10-01 retract_z held the Search Height and a run travelled
    between studs at Safe Z. Before 2026-09-22 safe_z held the Search Height.
    Retract Z starts where the travel already was, so nothing moves differently
    until someone lowers it."""
    old = _recipe(id="old", safe_z=10.0, high_z_clearance=40.0)
    del old["retract_z"]
    run_app.write([
        _recipe(id="sep22", safe_z=127.0, retract_z=25.4),
        old,
        _recipe(id="high", safe_z=60.0, retract_z=80.0),
        _recipe(id="new", safe_z=127.0, retract_z=50.0, search_z=25.4),
        _recipe(id="shot", name="Single Shot", system="single_shot", safe_z=10.0, retract_z=10.0),
    ])
    with run_app.module._rec_lock:
        run_app.module._recipes_load()

    by_id = {r["id"]: r for r in run_app.read()}
    heights = lambda r: (r["safe_z"], r["retract_z"], r.get("search_z"))
    assert heights(by_id["sep22"]) == (127.0, 127.0, 25.4)
    assert heights(by_id["old"]) == (50.0, 50.0, 10.0)
    assert "high_z_clearance" not in by_id["old"]
    # A search height above Safe Z is the one case Retract Z can't start at Safe Z.
    assert heights(by_id["high"]) == (60.0, 80.0, 80.0)
    assert heights(by_id["new"]) == (127.0, 50.0, 25.4)
    assert heights(by_id["shot"]) == (10.0, 10.0, None)


def test_recipe_save_stores_all_three_heights(run_app):
    run_app.write([_recipe(search_z=10.0)])
    form = {"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20",
            "safe_z": "127", "retract_z": "38.1", "search_z": "25.4"}
    run_app.client.post("/ui/recipes/save", data=form)
    saved = run_app.read()[0]
    assert (saved["safe_z"], saved["retract_z"], saved["search_z"]) == (127.0, 38.1, 25.4)

    # A form without them (the Single Shot settings) keeps what was saved.
    run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20", "safe_z": "127",
    })
    saved = run_app.read()[0]
    assert (saved["retract_z"], saved["search_z"]) == (38.1, 25.4)


def test_recipe_save_refuses_a_retract_z_below_the_search_height(run_app):
    run_app.write([_recipe(retract_z=60.0, search_z=10.0)])
    response = run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20",
        "safe_z": "60", "retract_z": "20", "search_z": "25.4",
    })
    assert "below the Search Height" in response.get_data(as_text=True)
    assert "X-Recipe-Id" not in response.headers
    saved = run_app.read()[0]
    assert (saved["retract_z"], saved["search_z"]) == (60.0, 10.0)


def test_recipe_save_refuses_a_safe_z_below_retract_z(run_app):
    """The end of a cycle lifts from Retract Z to Safe Z, so Safe Z below it
    would drive the head down toward the part on the way home."""
    run_app.write([_recipe(safe_z=127.0, retract_z=60.0, search_z=10.0)])
    response = run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20",
        "safe_z": "50", "retract_z": "60", "search_z": "10",
    })
    assert "below Retract Z" in response.get_data(as_text=True)
    assert "X-Recipe-Id" not in response.headers
    saved = run_app.read()[0]
    assert (saved["safe_z"], saved["retract_z"]) == (127.0, 60.0)


def test_recipe_save_reads_a_pre_retract_z_page_as_the_search_height(run_app):
    """A kiosk page loaded before the deploy still posts the Search Height as
    retract_z and no search_z. Saving it must not become the travel height."""
    run_app.write([_recipe(retract_z=50.0, search_z=10.0)])
    run_app.client.post("/ui/recipes/save", data={
        "recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20",
        "safe_z": "60", "retract_z": "25.4",
    })
    saved = run_app.read()[0]
    assert (saved["retract_z"], saved["search_z"]) == (50.0, 25.4)


def test_job_load_takes_all_three_heights_from_the_part(run_app):
    run_app.write([_recipe(safe_z=127.0, retract_z=38.1, search_z=25.4)])
    run_app.client.post("/ui/job/load", data={"recipe_id": "part-1", "arm_mode": "dry"})
    (_, kwargs), = run_app.job.loads
    assert (kwargs["safe_z"], kwargs["retract_z"], kwargs["search_z"]) == (127.0, 38.1, 25.4)


def test_job_manager_refuses_a_retract_z_below_the_search_height_at_load(run_app):
    job_manager = importlib.import_module("job_manager")
    manager = job_manager.JobManager.__new__(job_manager.JobManager)
    with pytest.raises(job_manager.JobError, match="Retract Z"):
        manager.load("part-1", "Bracket", [{"x": 300, "y": 300}], 1,
                     arm_mode="dry", safe_z=127.0, retract_z=20.0, search_z=25.4)


def test_job_manager_refuses_a_safe_z_below_retract_z_at_load(run_app):
    job_manager = importlib.import_module("job_manager")
    manager = job_manager.JobManager.__new__(job_manager.JobManager)
    with pytest.raises(job_manager.JobError, match="Safe Z"):
        manager.load("part-1", "Bracket", [{"x": 300, "y": 300}], 1,
                     arm_mode="dry", safe_z=50.0, retract_z=60.0, search_z=25.4)


@pytest.fixture
def goto(run_app, monkeypatch):
    """Posts to the designer's Goto and returns the Lua it would upload."""
    uploaded = []
    robot = run_app.module.robot
    zerozero = [400.0, -200.0, 50.0, 180.0, 0.0, 90.0]
    # The taught corner points, as offsets from zerozero. Not nominal and not
    # square, so a hard-coded 762 or a dropped skew shows up.
    taught = {"zerozero": (0.0, 0.0), "zerozero_fr": (736.6, 1.5),
              "zerozero_bl": (-2.0, 740.0), "zerozero_br": (762.0, 762.0)}
    frames = {"tool_wobj": (2, 2)}

    def teach_point_pose(name):
        if name not in taught:
            raise RuntimeError(f"Can't read taught point {name!r} (code -1)")
        dx, dy = taught[name]
        return [zerozero[0] + dx, zerozero[1] + dy, zerozero[2] + 3.0] + zerozero[3:]
    monkeypatch.setattr(robot, "upload_and_run",
                        lambda path: uploaded.append(Path(path).read_text(encoding="utf-8")))
    monkeypatch.setattr(robot, "get_universal_state",
                        lambda: UniversalRobotState(commands_available=True))
    monkeypatch.setattr(robot, "active_tool_wobj", lambda: frames["tool_wobj"])
    # The head starts 60 mm straight above zerozero, square to the bed.
    monkeypatch.setattr(robot, "jog_pose", lambda: [400.0, -200.0, 110.0, 180.0, 0.0, 90.0])
    monkeypatch.setattr(robot, "teach_point_pose", teach_point_pose)

    def post(**form):
        response = run_app.client.post("/ui/parts/goto", data={"safe_z": "60", "part_z": "0", **form})
        return response.get_data(as_text=True), uploaded

    post.frames = frames
    post.taught = taught
    return post


def test_goto_moves_to_the_stud_measured_from_the_parts_corner(goto):
    _, uploaded = goto(x="100", y="50", origin_corner="back_right")
    (program,) = uploaded
    assert "PointsOffsetEnable(0, 662.000, 712.000, 127.000, 0, 0, 0)" in program


def test_goto_to_a_corners_0_0_lands_on_its_taught_point(goto):
    """A front-right 0,0 went ~3 in past zerozero_fr when the corner came from an
    unmeasured 762 in .env (2026-09-25). It is the taught point now, skew and all."""
    _, uploaded = goto(x="0", y="0", origin_corner="front_right")
    (program,) = uploaded
    assert "PointsOffsetEnable(0, 736.600, 1.500, 127.000, 0, 0, 0)" in program


def test_goto_refuses_a_corner_whose_point_is_not_taught(goto):
    del goto.taught["zerozero_bl"]
    html, uploaded = goto(x="10", y="10", origin_corner="back_left")
    assert "Teach zerozero_bl" in html
    assert uploaded == []


def test_goto_refuses_a_corner_point_on_the_wrong_side_of_zerozero(goto):
    """What a point taught in another frame, or at the wrong stop, looks like."""
    goto.taught["zerozero_fr"] = (-736.6, 0.0)
    html, uploaded = goto(x="10", y="10", origin_corner="front_right")
    assert "should be right of zerozero" in html
    assert uploaded == []


def test_goto_parks_at_safe_z_not_the_run_heights(goto):
    """⌖ travels at Safe Z, the height that clears fixtures (owner, 2026-09-22).
    Retract Z and the Search Height are only for a run's moves between studs."""
    _, uploaded = goto(x="100", y="50", safe_z="127", part_z="2.54",
                       retract_z="76.2", search_z="50.8")
    (program,) = uploaded
    assert "PointsOffsetEnable(0, 100.000, 50.000, 129.540, 0, 0, 0)" in program
    assert "50.8" not in program and "53.34" not in program
    assert "76.2" not in program and "78.74" not in program


def test_goto_without_a_corner_is_front_left(goto):
    _, uploaded = goto(x="100", y="50")
    assert "PointsOffsetEnable(0, 100.000, 50.000, 127.000, 0, 0, 0)" in uploaded[0]


def test_goto_never_sweeps_in_joint_space(goto):
    """A PTP Goto swung the head into the arm's second joint (2026-09-25).
    Now it lifts straight up, travels level, and descends straight down."""
    _, uploaded = goto(x="44.7", y="304.8", safe_z="30")
    (program,) = uploaded
    assert "PTP" not in program and "MoveCart" not in program
    lines = [l for l in program.splitlines() if l.startswith("PointsOffsetEnable")]
    assert lines == [
        "PointsOffsetEnable(0, 0.000, 0.000, 127.000, 0, 0, 0)",      # straight up
        "PointsOffsetEnable(0, 44.700, 304.800, 127.000, 0, 0, 0)",   # level
        "PointsOffsetEnable(0, 44.700, 304.800, 30.000, 0, 0, 0)",    # straight down
    ]
    assert program.index("SetAnticollision(") < program.index("Lin(")


def test_goto_refuses_a_stud_inside_the_base_no_go_zone(goto, monkeypatch):
    """fabtech polygon's stud 5, 175 mm from J1, nearly put the head into J2."""
    monkeypatch.setenv("WELDFLEX_BASE_X_MM", "-130")
    monkeypatch.setenv("WELDFLEX_BASE_Y_MM", "320")
    monkeypatch.setenv("WELDFLEX_BASE_KEEPOUT_MM", "250")
    html, uploaded = goto(x="44.7", y="304.8")
    assert "within 175 mm of the robot base" in html
    assert uploaded == []


def test_job_load_refuses_a_part_inside_the_base_no_go_zone(run_app, monkeypatch):
    monkeypatch.setenv("WELDFLEX_BASE_X_MM", "-130")
    monkeypatch.setenv("WELDFLEX_BASE_Y_MM", "320")
    monkeypatch.setenv("WELDFLEX_BASE_KEEPOUT_MM", "250")
    job_manager = importlib.import_module("job_manager")
    manager = job_manager.JobManager.__new__(job_manager.JobManager)
    with pytest.raises(job_manager.JobError, match="Stud 2"):
        manager.load("part-1", "Bracket", [{"x": 300, "y": 300}, {"x": 44.7, "y": 304.8}], 1,
                     arm_mode="dry")


def test_goto_refuses_the_wrong_active_frame_without_moving(goto):
    goto.frames["tool_wobj"] = (1, 0)
    html, uploaded = goto(x="100", y="50")
    assert "tool 1 / wobj 0" in html
    assert uploaded == []


def test_goto_refuses_a_stud_that_would_flip_across_the_bed(goto):
    html, uploaded = goto(x="800", y="50", origin_corner="front_right")
    assert "along X" in html
    assert uploaded == []


@pytest.mark.parametrize("corner, origin", [
    ("front_left", (20, 220)),
    ("front_right", (220, 220)),
    ("back_left", (20, 20)),
    ("back_right", (220, 20)),
])
def test_the_parts_page_preview_puts_0_0_on_the_parts_corner(run_app, corner, origin):
    """It used to put 0,0 bottom-right with +X running left — the part designer
    draws zerozero bottom-left. Both now draw the bed as the operator faces it."""
    html = run_app.client.get(
        "/ui/studs-preview", query_string={"studs_text": "0,0", "origin_corner": corner}
    ).get_data(as_text=True)
    cx, cy = origin
    assert f'<circle cx="{cx}" cy="{cy}" r="5" class="plot-origin">' in html
    # A stud at the part's 0,0 sits on that dot.
    assert f'<circle cx="{cx}.0" cy="{cy}.0" r="5" class="plot-point">' in html


def test_saving_a_part_by_name_never_overwrites_the_single_shot_record(run_app):
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot")])
    run_app.client.post(
        "/ui/recipes/save", data={"recipe_name": "Single Shot", "studs_text": "1,2"}
    )
    recipes = run_app.read()
    assert len(recipes) == 2
    assert next(r for r in recipes if r.get("system"))["studs"] == [{"x": 10, "y": 20}]


SHOT_FORM = {"recipe_id": "shot", "recipe_name": "Single Shot"}


def test_single_shot_settings_render_the_target_as_separate_x_and_y(run_app):
    """One "X, Y" box could not be filled in on the kiosk: its number pad has no comma."""
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot",
                           studs=[{"x": 254.0, "y": 276.5}])])
    html = run_app.client.get("/operator/single-shot").get_data(as_text=True)
    assert re.search(r'<input[^>]*\bname="target_x"[^>]*\bvalue="254.0"', html)
    assert re.search(r'<input[^>]*\bname="target_y"[^>]*\bvalue="276.5"', html)
    assert 'name="studs_text"' not in html


def test_single_shot_settings_save_the_target_from_x_and_y(run_app):
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot")])
    response = run_app.client.post(
        "/ui/recipes/save", data={**SHOT_FORM, "target_x": "215.265", "target_y": "762"}
    )
    assert response.headers.get("X-Recipe-Id") == "shot"
    assert run_app.read()[0]["studs"] == [{"x": 215.265, "y": 762.0}]


@pytest.mark.parametrize("target, error", [
    ({"target_x": "", "target_y": "20"}, "Target X is required"),
    ({"target_x": "10"}, "Target Y is required"),
    ({"target_x": "10, 20", "target_y": "20"}, "Target X must be a number"),
    ({"target_x": "-0.5", "target_y": "20"}, "Target X must be 0 to 762 mm"),
    ({"target_x": "10", "target_y": "762.1"}, "Target Y must be 0 to 762 mm"),
])
def test_single_shot_settings_refuse_a_bad_target_and_keep_the_saved_one(run_app, target, error):
    """A target that didn't parse used to save as no target, behind a success toast."""
    run_app.write([_recipe(id="shot", name="Single Shot", system="single_shot")])
    response = run_app.client.post(
        "/ui/recipes/save", data={**SHOT_FORM, **target, "safe_z": "99"}
    )
    assert error in response.get_data(as_text=True)
    # No X-Recipe-Id is what keeps the settings modal open over the error toast.
    assert "X-Recipe-Id" not in response.headers
    saved = run_app.read()[0]
    assert saved["studs"] == [{"x": 10, "y": 20}]
    assert saved["safe_z"] == 60.0


def test_recipe_save_refuses_unparseable_studs_instead_of_erasing_them(run_app):
    """The parts page posts a half-filled coordinate row as "30," — that used to
    save the part with no studs at all."""
    run_app.write([_recipe()])
    response = run_app.client.post(
        "/ui/recipes/save",
        data={"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20\n30,"},
    )
    assert "Invalid line" in response.get_data(as_text=True)
    assert run_app.read()[0]["studs"] == [{"x": 10, "y": 20}]


def test_single_shot_target_range_is_the_part_designers_bed(run_app):
    js = Path(run_app.module.__file__).parent / "static" / "js" / "bed_map.js"
    bed = re.search(r"const BED = ([\d.]+);", js.read_text(encoding="utf-8"))
    assert float(bed.group(1)) == run_app.module.BED_MM


def test_single_shot_requires_live_or_dry_and_loads_one_cycle(run_app):
    run_app.write([_recipe(
        id="shot", name="Single Shot", system="single_shot",
        safe_z=10.0, studs=[{"x": 5, "y": 6}], di_check=False,
    )])
    refused = run_app.client.post("/ui/single-shot/fire", data={})
    assert b"Live or Dry" in refused.data
    assert run_app.job.loads == [] and run_app.job.starts == 0

    run_app.client.post("/ui/single-shot/fire", data={"arm_mode": "live"})
    (args, kwargs), = run_app.job.loads
    assert args == ("__single_shot__", "Single Shot", [{"x": 5, "y": 6}], 1)
    assert kwargs["kind"] == "single_shot"
    assert kwargs["arm_mode"] == "live"
    assert kwargs["di_check"] is False
    assert run_app.job.starts == 1
