import threading
import time
import xmlrpc.client
from types import SimpleNamespace
from xmlrpc.server import SimpleXMLRPCServer

import pytest

from robot_feed import FeedSnapshot, FeedStats
from robot_link import (
    CNDE_PERIOD_MS,
    CNDE_PORT,
    ConnState,
    RobotLink,
    RobotUnreachable,
    _ClientHandle,
    _SdkWorker,
    _TimeoutTransport,
)
from robot_service import WeldFlexRobotService


def test_weld_probe_uses_short_timeout_for_live_telemetry(monkeypatch):
    service = WeldFlexRobotService("127.0.0.1")
    calls = []

    def fake_call(fn, timeout=5.0, retries=3, priority=0, coalesce_key=None, rpc_timeout=None):
        calls.append((timeout, retries, priority, coalesce_key, rpc_timeout))
        return 0, 1

    monkeypatch.setattr(service, "_call", fake_call)

    service.weld_probe(stud_di=1, ready_di=0, sysvar_slots=(1, 2))

    assert calls == [
        (0.75, 1, 2, "weld-sysvar:1", 0.5),
        (0.75, 1, 2, "weld-sysvar:2", 0.5),
    ]


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
    cnde = _LiveCnde()
    try:
        pkg = SimpleNamespace(program_state=2, main_code=102, sub_code=4)
        client = SimpleNamespace(_cnde_client=cnde, robot_state_pkg=pkg)

        state, st_src = link._read_program_state(client)
        main, sub, flt_src = link._read_fault_codes(client)

        assert (state, st_src) == (2, "cnde")
        assert (main, sub, flt_src) == (102, 4, "cnde")
    finally:
        cnde.stop()


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



class _LiveCnde:
    """A CNDE client whose receiver thread is running."""

    def __init__(self):
        self._robot_state_run_flag = True
        self._stop = threading.Event()
        self._recv_thread = threading.Thread(target=self._stop.wait, daemon=True)
        self._recv_thread.start()

    def stop(self):
        """Break out of the recv loop the way Robot.py:1826-1833 does.

        Note what it does *not* do: clear `_robot_state_run_flag`, or null
        `_tcp_socket`. Both stay truthy over the dead stream.
        """
        self._stop.set()
        self._recv_thread.join(timeout=1.0)


class _DeadProxy:
    """Every call fails at the transport — the link itself is gone."""

    def __call__(self, attribute):
        assert attribute == "transport"
        return _TimeoutTransport(12.0)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise RobotUnreachable("XML-RPC to 127.0.0.1 failed: timed out")

        return call


class _GarbageProxy:
    """Calls reach the controller and come back unusable. Not a link failure."""

    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise ValueError("unexpected response shape")

        return call


def _cached_pkg():
    """A CNDE struct holding the last values it received before the stream died."""
    return SimpleNamespace(program_state=1, robot_state=1, main_code=0, sub_code=0)


def test_probe_fails_when_xmlrpc_is_dead_even_though_cnde_answers():
    """The heartbeat must prove the command channel, not just that state is readable.

    `commands_available` is gated on this probe alone. Both cache readers in
    `_probe_body` can be satisfied without touching XML-RPC, so if the one real
    round trip is allowed to fail silently the link reports CONNECTED over a dead
    command channel — the exact case `telemetry` state exists to surface.
    """
    link = RobotLink("127.0.0.1")
    cnde = _LiveCnde()
    try:
        client = SimpleNamespace(
            robot=_DeadProxy(), _cnde_client=cnde, robot_state_pkg=_cached_pkg()
        )
        assert link._cnde_streaming(cnde) is True

        try:
            link._probe_body(client)
        except RobotUnreachable:
            pass
        else:
            raise AssertionError("probe reported success over a dead XML-RPC channel")
    finally:
        cnde.stop()


def test_cnde_cache_is_distrusted_once_its_receiver_thread_exits():
    """`_robot_state_run_flag` latches True forever; the recv thread does not."""
    link = RobotLink("127.0.0.1")
    cnde = _LiveCnde()
    cnde.stop()

    assert cnde._robot_state_run_flag is True  # the SDK never clears it
    assert link._cnde_streaming(cnde) is False

    client = SimpleNamespace(
        robot=_GarbageProxy(), _cnde_client=cnde, robot_state_pkg=_cached_pkg()
    )
    # With the cache refused, the frozen program_state must not be served as "cnde".
    assert link._read_program_state(client) == (1, "cache")
    assert link._read_fault_codes(client) == (None, None, "none")


def test_fresh_feed_heartbeat_only_issues_one_rpc(monkeypatch):
    link = RobotLink("127.0.0.1")
    calls = []

    class Proxy(_DeadProxy):
        def GetCurrentLine(self):
            calls.append("line")
            return 0, 42

    monkeypatch.setattr(link, "feed_snapshot", lambda: FeedSnapshot(
        fields={"program_state": 2, "main_errcode": 0, "sub_errcode": 0},
        received_monotonic=time.monotonic(),
    ))
    reading = link._probe_body(SimpleNamespace(robot=Proxy()))
    assert calls == ["line"]
    assert reading["state_raw"] == 2
    assert reading["state_src"] == "8083"


def test_transport_read_timeout_is_restored_after_failure():
    transport = _TimeoutTransport(12.0)
    with pytest.raises(RuntimeError):
        with transport.limit_timeout(0.5):
            assert transport.make_connection("127.0.0.1").timeout == 0.5
            raise RuntimeError("read failed")
    assert transport.make_connection("127.0.0.1").timeout == 12.0
    transport.close()


def test_expired_queued_call_is_not_executed():
    link = RobotLink("127.0.0.1")
    link._handle = _ClientHandle(gen=1, rpc=object(), ip="127.0.0.1")
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocking():
        started.set()
        release.wait(2.0)

    active = link._worker.submit(blocking)
    try:
        assert started.wait(1.0)
        with pytest.raises(RuntimeError, match="did not respond"):
            link.call(lambda robot: calls.append("expired"), timeout=0.02, retries=1)
        release.set()
        active.result(1.0)
        link._worker.submit(lambda: None, priority=3).result(1.0)
        assert calls == []
    finally:
        release.set()
        link._worker.retire()


def test_slow_telemetry_socket_releases_worker_for_command():
    entered = threading.Event()
    release = threading.Event()
    server = SimpleXMLRPCServer(("127.0.0.1", 0), logRequests=False)

    def slow_read():
        entered.set()
        release.wait(2.0)
        return [0, 1]

    server.register_function(slow_read, "GetSysVarValue")
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()
    transport = _TimeoutTransport(12.0)
    proxy = xmlrpc.client.ServerProxy(
        f"http://127.0.0.1:{server.server_address[1]}", transport=transport,
    )
    link = RobotLink("127.0.0.1")
    handle = _ClientHandle(gen=1, rpc=SimpleNamespace(robot=proxy), ip="127.0.0.1")
    link._handle = handle
    failures = []

    def sample():
        try:
            link.call(
                lambda robot: robot.robot.GetSysVarValue(),
                timeout=1.0, rpc_timeout=0.1, retries=1, priority=2,
            )
        except RobotUnreachable as exc:
            failures.append(exc)

    sample_thread = threading.Thread(target=sample, daemon=True)
    try:
        sample_thread.start()
        assert entered.wait(1.0)
        command = link._worker.submit(lambda: "command", priority=0)
        assert command.result(1.0) == "command"
        assert not release.is_set()
        sample_thread.join(1.0)
        assert len(failures) == 1
        assert transport._timeout == 12.0
    finally:
        release.set()
        sample_thread.join(1.0)
        server_thread.join(1.0)
        server.server_close()
        transport.close()
        link._worker.retire()
