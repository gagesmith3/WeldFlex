"""Points-page moves: the plan's geometry, the program it becomes, and the route's
refusals. The robot is a recorder throughout; nothing here reaches a controller.
"""

import importlib
from types import SimpleNamespace

import pytest

import point_moves
from point_moves import PointMoveError, build_program, plan_move
from robot_service import UniversalRobotState, WeldFlexRobotService

SQUARE = [180.0, 0.0, 90.0]
ZZ = [400.0, -200.0, 50.0] + SQUARE


def _pose(x, y, z, rot=SQUARE):
    return [x, y, z] + list(rot)


def test_lift_travel_descend_are_offsets_from_zerozero():
    plan = plan_move("homewf", "to", _pose(450, -150, 60), ZZ, _pose(600, 0, 110))
    assert plan.start == (50, 50, 10)
    assert plan.target == (200, 200, 60)
    assert plan.travel_z == point_moves.TRAVEL_HEIGHT_MM
    assert plan.lift_mm == pytest.approx(117)
    assert plan.descend_mm == pytest.approx(67)

    program = build_program(plan)
    lines = program.splitlines()
    # Lift is straight up: same XY as the start, only Z changes.
    assert "PointsOffsetEnable(0, 50.000, 50.000, 127.000, 0, 0, 0)" in lines
    # Travel is level: the point's XY at the same height.
    assert "PointsOffsetEnable(0, 200.000, 200.000, 127.000, 0, 0, 0)" in lines
    assert lines[-1] == f"Lin(homewf, {point_moves.DESCEND_SPEED_PCT}, -1, 0, 0)"
    assert "PTP" not in program and "MoveCart" not in program


def test_collision_guard_is_reset_before_any_motion():
    program = build_program(plan_move("zerozero", "to", _pose(500, -100, 80), ZZ, ZZ))
    first_motion = program.index("Lin(")
    assert program.index("CustomCollisionDetectionEnd()") < first_motion
    assert program.index("SetAnticollision(0, {3, 3, 3, 3, 3, 3}, 0)") < first_motion


def test_never_drops_to_travel_when_already_high():
    plan = plan_move("zerozero", "to", _pose(500, -100, 350), ZZ, ZZ)
    assert plan.travel_z == pytest.approx(300)
    assert plan.lift_mm == pytest.approx(0)


def test_travels_at_the_point_height_when_it_is_higher():
    plan = plan_move("homewf", "to", _pose(450, -150, 60), ZZ, _pose(600, 0, 250))
    assert plan.travel_z == pytest.approx(200)
    assert plan.descend_mm == pytest.approx(0)


def test_above_mode_stops_at_travel_height():
    plan = plan_move("zerozero", "above", _pose(500, -100, 80), ZZ, ZZ)
    assert not plan.do_descend
    assert "Lin(zerozero, 5" not in build_program(plan)


def test_tilted_head_is_squared_even_with_no_lift():
    plan = plan_move("zerozero", "above", _pose(400, -200, 177, [170, 5, 90]), ZZ, ZZ)
    assert plan.lift_mm == pytest.approx(0)
    assert plan.do_lift


def test_angle_wrap_counts_as_square():
    plan = plan_move("zerozero", "above", _pose(400, -200, 177, [-180, 0, 90]), ZZ, ZZ)
    assert not plan.do_lift and not plan.do_travel and not plan.do_descend


def test_head_below_the_bed_is_refused():
    with pytest.raises(PointMoveError, match="below zerozero"):
        plan_move("zerozero", "to", _pose(400, -200, -100), ZZ, ZZ)


def test_unknown_mode_is_refused():
    with pytest.raises(PointMoveError):
        plan_move("zerozero", "sideways", _pose(400, -200, 80), ZZ, ZZ)


# ── route layer ──

class FakeRobot:
    def __init__(self, tool_wobj=(2, 2), commands=True):
        self.tool_wobj = tool_wobj
        self.commands = commands
        self.uploaded = []
        self.stops = 0

    def get_universal_state(self):
        return UniversalRobotState(commands_available=self.commands)

    def active_tool_wobj(self):
        return self.tool_wobj

    def jog_pose(self):
        return _pose(450, -150, 60)

    def teach_point_pose(self, name):
        return {"zerozero": ZZ, "homewf": _pose(600, 0, 110)}[name]

    def upload_and_run(self, path):
        with open(path, encoding="utf-8") as f:
            self.uploaded.append(f.read())

    def stop_program(self):
        self.stops += 1


class IdleJob:
    def snapshot(self):
        return SimpleNamespace(active=False)

    def stop(self):
        from job_manager import JobError
        raise JobError("no job")


@pytest.fixture
def points_app(monkeypatch):
    monkeypatch.setattr(WeldFlexRobotService, "start", lambda self: None)
    module = importlib.import_module("app")
    robot = FakeRobot()
    # Patch the calls the Points routes make; the rest of the real service stays,
    # because every render's context processor reads it.
    for name in ("get_universal_state", "active_tool_wobj", "jog_pose",
                 "teach_point_pose", "upload_and_run", "stop_program"):
        monkeypatch.setattr(module.robot, name, getattr(robot, name))
    monkeypatch.setattr(module, "job", IdleJob())
    return SimpleNamespace(client=module.app.test_client(), robot=robot)


def test_move_uploads_the_planned_program(points_app):
    resp = points_app.client.post("/ui/points/move", data={"point": "homewf", "mode": "to"})
    assert b"OK" in resp.data
    assert len(points_app.robot.uploaded) == 1
    assert "Lin(homewf" in points_app.robot.uploaded[0]


def test_wrong_active_frame_refuses_without_moving(points_app):
    points_app.robot.tool_wobj = (0, 0)
    resp = points_app.client.post("/ui/points/move", data={"point": "homewf", "mode": "to"})
    assert b"tool 0 / wobj 0 active" in resp.data
    assert points_app.robot.uploaded == []


def test_untaught_point_refuses(points_app):
    resp = points_app.client.post("/ui/points/move", data={"point": "pitstop", "mode": "to"})
    assert b"not taught" in resp.data
    assert points_app.robot.uploaded == []


def test_no_commands_refuses(points_app):
    points_app.robot.commands = False
    resp = points_app.client.post("/ui/points/move", data={"point": "zerozero", "mode": "to"})
    assert b"accepting commands" in resp.data
    assert points_app.robot.uploaded == []


def test_plan_preview_shows_the_distances(points_app):
    resp = points_app.client.get("/ui/points/plan?point=homewf")
    assert b"points-plan-ok" in resp.data
    assert b"117 mm" in resp.data and b"67 mm" in resp.data


def test_plan_preview_error_leaves_moves_disabled(points_app):
    points_app.robot.tool_wobj = (1, 2)
    resp = points_app.client.get("/ui/points/plan?point=homewf")
    assert b"points-plan-ok" not in resp.data


def test_stop_never_refuses(points_app):
    resp = points_app.client.post("/ui/points/stop")
    assert b"OK" in resp.data
    assert points_app.robot.stops == 1
