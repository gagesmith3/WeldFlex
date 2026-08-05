import threading
import time
from types import SimpleNamespace

from robot_feed import FeedSnapshot, FeedStats
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
    assert CNDE_PORT == 20005
    state_names = [s.name for s in configured[0][0]]
    assert "FtSensorData" in state_names
    assert "ProgramState" in state_names
    assert "MainCode" in state_names
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


def test_read_program_state_and_fault_codes_prefer_cnde_stream():
    link = RobotLink("127.0.0.1")
    cnde = SimpleNamespace(_robot_state_run_flag=True)
    pkg = SimpleNamespace(program_state=2, main_code=102, sub_code=4)
    client = SimpleNamespace(_cnde_client=cnde, robot_state_pkg=pkg)

    state, st_src = link._read_program_state(client)
    main, sub, flt_src = link._read_fault_codes(client)

    assert (state, st_src) == (2, "cnde")
    assert (main, sub, flt_src) == (102, 4, "cnde")


class _FakeFeed:
    """Records lifecycle calls so the wiring can be asserted without a socket."""

    def __init__(self):
        self.calls = []
        self._snapshot = FeedSnapshot()

    def start(self, ip):
        self.calls.append(("start", ip))

    def retarget(self, ip):
        self.calls.append(("retarget", ip))

    def stop(self, timeout=3.0):
        self.calls.append(("stop", None))

    def snapshot(self):
        return self._snapshot

    def stats(self):
        return FeedStats(state="streaming", ip="127.0.0.1")


def test_constructing_a_link_does_not_open_the_status_feed():
    """No socket until the operator asks for a connection."""
    link = RobotLink("127.0.0.1")

    assert link._feed.is_streaming() is False
    assert not any(t.name == "robot-feed" for t in threading.enumerate())


def test_status_feed_follows_the_operator_intent(monkeypatch):
    """Connect/disconnect/retarget drive the feed, not the RPC client's success.

    The feed is started on intent rather than after a successful RPC connect so
    telemetry still arrives when the command channel is down — the two failure
    modes are independent and must stay that way.

    The supervisor is stubbed out because this is about the wiring, not about
    reaching a controller: letting it run would build a real SDK client and dial
    a real socket for a question neither one answers.
    """
    link = RobotLink("127.0.0.1")
    feed = _FakeFeed()
    link._feed = feed
    monkeypatch.setattr(link, "start", lambda connect=True: None)

    link.connect()
    link.set_ip("127.0.0.2")
    link.disconnect()

    assert feed.calls == [
        ("start", "127.0.0.1"),
        ("retarget", "127.0.0.2"),
        ("stop", None),
    ]


def test_feed_snapshot_is_not_filtered_by_the_rpc_generation():
    """An XML-RPC reconnect says nothing about whether the feed is still good."""
    link = RobotLink("127.0.0.1")
    feed = _FakeFeed()
    feed._snapshot = FeedSnapshot(
        fields={"program_state": 2}, received_monotonic=time.monotonic(), generation=1
    )
    link._feed = feed
    link._gen = 99  # RPC client has reconnected many times since

    assert link.feed_snapshot().program_state == 2
    assert link.feed_stats()["state"] == "streaming"


def test_thread_report_counts_the_feed_thread():
    """Without the prefix in the census, a leaked feed thread is invisible."""
    link = RobotLink("127.0.0.1")
    assert not any(n.startswith("robot-feed") for n in link.thread_report()["sdk"])

    link._feed.start("127.0.0.1")
    try:
        assert any(n.startswith("robot-feed") for n in link.thread_report()["sdk"])
    finally:
        link._feed.stop()


def test_call_retries_transient_error_without_invalidating_generation():
    link = RobotLink("127.0.0.1")
    handle = _ClientHandle(gen=1, rpc=object(), ip="127.0.0.1")
    link._handle = handle

    attempt_counter = [0]

    def flakey_fn(rpc):
        attempt_counter[0] += 1
        if attempt_counter[0] == 1:
            raise OSError("Transient socket glitch")
        return [0, "success"]

    res = link.call(flakey_fn, timeout=1.0, retries=2, label="test_call")

    assert res == [0, "success"]
    assert attempt_counter[0] == 2
    assert link._handle is handle  # Handle was NOT invalidated on attempt 1

