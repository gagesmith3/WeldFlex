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

from robot_service import WeldFlexRobotService


class RecordingJob:
    """Stands in for JobManager: records every load and start, runs nothing."""

    def __init__(self):
        self.loads = []
        self.starts = 0

    def load(self, *args, **kwargs):
        self.loads.append((args, kwargs))

    def start(self):
        self.starts += 1


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
@pytest.mark.parametrize("di_check", [True, False])
def test_job_load_takes_arm_mode_from_the_run_and_di_check_from_the_part(run_app, arm_mode, di_check):
    run_app.write([_recipe(di_check=di_check)])
    response = run_app.client.post(
        "/ui/job/load", data={"recipe_id": "part-1", "cycles": "3", "arm_mode": arm_mode}
    )
    assert response.get_json() == {"ok": True}
    (args, kwargs), = run_app.job.loads
    assert args[0] == "part-1" and args[1] == "Bracket" and args[3] == 3
    assert kwargs["arm_mode"] == arm_mode
    assert kwargs["di_check"] is di_check
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
    assert by_id["a"]["di_check"] is True
    assert by_id["l"]["di_check"] is False
    assert by_id["f"]["system"] == "single_shot"
    assert all("welder_profile" not in recipe for recipe in run_app.read())
    assert [r["id"] for r in run_app.module._hide_system_recipes(recipes)] == ["a", "l"]


def test_recipe_save_sets_di_check_and_keeps_it_when_the_form_omits_it(run_app):
    run_app.write([_recipe()])
    form = {"recipe_id": "part-1", "recipe_name": "Bracket", "studs_text": "10,20"}
    run_app.client.post("/ui/recipes/save", data={**form, "di_check": "0"})
    assert run_app.read()[0]["di_check"] is False

    # The part designer and the Single Shot settings form post without every
    # field; a missing di_check must not quietly turn the checks back on.
    run_app.client.post("/ui/recipes/save", data=form)
    assert run_app.read()[0]["di_check"] is False


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
    js = Path(run_app.module.__file__).parent / "static" / "js" / "part_designer.js"
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
