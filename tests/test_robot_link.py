import threading
from types import SimpleNamespace

from robot_link import CNDE_PERIOD_MS, CNDE_PORT, ConnState, RobotLink, _ClientHandle, _SdkWorker
from robot_service import WeldFlexRobotService


def test_weld_probe_uses_short_timeout_for_live_telemetry(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    def fake_call(fn, timeout=5.0, retries=3, priority=0, coalesce_key=None):
        calls.append((timeout, retries, priority, coalesce_key))
        return [], None

    monkeypatch.setattr(service, "_call", fake_call)

    service.weld_probe(stud_di=1, ready_di=0, sysvar_slots=(1, 2))

    assert calls == [(3.0, 1, 2, "weld-detail")]


def test_sdk_worker_prioritizes_core_probe_over_queued_detail_telemetry():
    worker = _SdkWorker("test-robot-sdk")
    started = threading.Event()
    release = threading.Event()
    order = []

    def command():
        started.set()
        release.wait(1.0)
        order.append("command")

    def detail():
        order.append("detail")

    def probe():
        order.append("probe")

    command_future = worker.submit(command, label="command", priority=0)
    assert started.wait(1.0)
    detail_future = worker.submit(detail, label="detail", priority=2)
    probe_future = worker.submit(probe, label="probe", priority=1)
    release.set()

    command_future.result(1.0)
    probe_future.result(1.0)
    detail_future.result(1.0)
    worker.retire()

    assert order == ["command", "probe", "detail"]


def test_sdk_worker_coalesces_matching_queued_telemetry():
    worker = _SdkWorker("test-robot-sdk")
    started = threading.Event()
    release = threading.Event()
    samples = []

    def command():
        started.set()
        release.wait(1.0)

    def detail():
        samples.append("sampled")
        return 42

    command_future = worker.submit(command, label="command", priority=0)
    assert started.wait(1.0)
    first = worker.submit(detail, label="detail", priority=2, coalesce_key="detail:1")
    second = worker.submit(detail, label="detail", priority=2, coalesce_key="detail:1")
    release.set()

    command_future.result(1.0)
    assert first is second
    assert first.result(1.0) == 42
    worker.retire()
    assert samples == ["sampled"]


def test_fast_heartbeat_queues_probe_on_sdk_worker_when_busy(monkeypatch):
    link = RobotLink("127.0.0.1")
    link._enabled = True
    link._fast_heartbeat = True
    link._handle = _ClientHandle(gen=1, rpc=object(), ip="127.0.0.1")

    monkeypatch.setattr(link._worker, "is_busy", lambda: True)
    seen = []

    def fake_probe_body(client):
        seen.append((client, threading.current_thread().name))
        return {
            "latency_ms": 1.5,
            "state_raw": 2,
            "state_src": "rpc",
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
            "fault_src": "none",
        }

    monkeypatch.setattr(link, "_probe_body", fake_probe_body)

    link._tick()

    assert seen == [(link._handle.rpc, "robot-sdk-0")]
    snap = link.snapshot()
    assert snap.state == ConnState.CONNECTED.value
    assert snap.program_state_raw == 2
    assert snap.current_line == 42


def test_cnde_force_frame_is_cached_for_only_the_active_generation():
    link = RobotLink("127.0.0.1")
    client = object()
    link._handle = _ClientHandle(gen=3, rpc=client, ip="127.0.0.1")
    link._gen = 3

    link._on_cnde_state(
        client,
        SimpleNamespace(ft_sensor_data=(1, 2, 3, 4, 5, 6)),
    )

    reading = link.force_snapshot()
    assert reading.values == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert reading.generation == 3
    assert reading.source == "cnde"
    assert reading.age_s() is not None
    assert reading.is_fresh()

    link._on_cnde_state(
        object(),
        SimpleNamespace(ft_sensor_data=(7, 8, 9, 10, 11, 12)),
    )
    assert link.force_snapshot() == reading


def test_retired_probe_result_does_not_refresh_replacement_generation(monkeypatch):
    link = RobotLink("127.0.0.1")
    old_handle = _ClientHandle(gen=1, rpc=object(), ip="127.0.0.1")
    replacement = _ClientHandle(gen=2, rpc=object(), ip="127.0.0.1")
    link._enabled = True
    link._handle = old_handle
    link._gen = 1

    def fake_run_probe(client):
        assert client is old_handle.rpc
        with link._state_lock:
            link._handle = replacement
            link._gen = replacement.gen
            link._publish_locked()
        return {
            "latency_ms": 1.5,
            "state_raw": 2,
            "state_src": "rpc",
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
            "fault_src": "none",
        }

    monkeypatch.setattr(link, "_run_probe", fake_run_probe)

    link._probe(old_handle)

    snap = link.snapshot()
    assert snap.generation == replacement.gen
    assert snap.current_line is None
    assert snap.program_state_raw is None


def test_open_discards_client_when_retargeted_during_initial_probe(monkeypatch):
    link = RobotLink("127.0.0.1")
    link._enabled = True
    client = object()
    torn_down = []

    monkeypatch.setattr("robot_link.Robot.RPC", lambda ip: client)
    monkeypatch.setattr("robot_link.harden_client", lambda candidate, ip: None)
    monkeypatch.setattr(
        link,
        "_run_probe",
        lambda candidate: {
            "latency_ms": 1.5,
            "state_raw": 2,
            "state_src": "rpc",
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
            "fault_src": "none",
        },
    )
    monkeypatch.setattr(link, "_teardown_raw", lambda candidate, close_rpc: torn_down.append(candidate))

    with link._state_lock:
        link._pending_ip = "127.0.0.2"

    link._attempt_open()

    assert link.snapshot().connected is False
    assert link._handle is None
    assert torn_down == [client]


def test_open_configures_cnde_port_before_creating_sdk_client(monkeypatch):
    link = RobotLink("127.0.0.1")
    link._enabled = True
    client = object()
    observed_ports = []
    configured = []

    class FakeRPC:
        ROBOT_CNDE_PORT = None

        def __new__(cls, ip):
            observed_ports.append(cls.ROBOT_CNDE_PORT)
            return client

    monkeypatch.setattr("robot_link.Robot.RPC", FakeRPC)
    monkeypatch.setattr(
        "robot_link.Robot.SetRobotRealtimeStateConfig",
        lambda states, period: configured.append((states, period)),
    )
    monkeypatch.setattr("robot_link.harden_client", lambda candidate, ip: None)
    monkeypatch.setattr(
        link,
        "_run_probe",
        lambda candidate: {
            "latency_ms": 1.5,
            "state_raw": 2,
            "state_src": "rpc",
            "line": 42,
            "fault_main": None,
            "fault_sub": None,
            "fault_src": "none",
        },
    )

    link._attempt_open()

    assert observed_ports == [CNDE_PORT]
    assert configured[0][0][0].name == "FtSensorData"
    assert configured[0][1] == CNDE_PERIOD_MS


def test_malformed_raw_state_reply_does_not_disable_future_samples():
    link = RobotLink("127.0.0.1")
    link._raw_state_supported = True
    client = SimpleNamespace(
        robot=SimpleNamespace(GetProgramState=lambda: [0]),
        robot_state_pkg=None,
    )

    state, source = link._read_program_state(client)

    assert (state, source) == (None, "none")
    assert link._raw_state_supported is True


def test_malformed_raw_fault_reply_does_not_disable_future_samples():
    link = RobotLink("127.0.0.1")
    link._raw_fault_supported = True
    client = SimpleNamespace(
        robot=SimpleNamespace(GetRobotErrorCode=lambda: [0, 0]),
        robot_state_pkg=None,
    )

    main, sub, source = link._read_fault_codes(client)

    assert (main, sub, source) == (None, None, "none")
    assert link._raw_fault_supported is True
