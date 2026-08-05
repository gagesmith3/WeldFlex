"""The port-8083 status feed, over a real loopback socket.

Runs against `tools/stub_robot.py`'s feed server rather than a purpose-built
double, for the reason conftest gives: the stub is what a developer points the
app at, so if it drifts from the decoder these tests should be what catches it.

Everything here is timing-dependent by nature — it is a push stream — so
assertions go through `wait_for` rather than sleeping a fixed amount and hoping.
"""

import socket
import threading
import time

import pytest

import frame_8083 as f8
from robot_feed import FeedState, StatusFeed
from tools.stub_robot import StatusFeedServer, StubController

TIMEOUT = 5.0
PERIOD = 0.01  # frames arrive far faster than production, to keep tests quick


def wait_for(predicate, timeout: float = TIMEOUT, interval: float = 0.01):
    """Poll until `predicate` returns something truthy, else fail with context."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


@pytest.fixture
def stub():
    return StubController()


@pytest.fixture
def server(stub):
    srv = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def feed(server):
    client = StatusFeed(port=server.port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    yield client
    client.stop()


def started(feed, ip: str = "127.0.0.1") -> StatusFeed:
    feed.start(ip)
    assert wait_for(lambda: feed.snapshot().is_fresh()), "no frame arrived"
    return feed


# --- happy path -----------------------------------------------------------


def test_feed_receives_and_decodes_frames(feed, stub):
    stub.program_state = 2
    stub.current_line = 47
    started(feed)

    snap = feed.snapshot()
    assert snap.program_state == 2
    assert snap.current_line == 47
    assert snap.program_name == "WeldFlex"
    assert snap.data_len == f8.EXPECTED_DATA_LEN
    assert snap.little_endian is True
    assert feed.is_streaming()


def test_stats_report_a_healthy_stream(feed):
    started(feed)
    assert wait_for(lambda: feed.stats().frames > 5)

    stats = feed.stats()
    assert stats.state == FeedState.STREAMING.value
    assert stats.reader["checksum_fail"] == 0
    assert stats.reader["resync_bytes"] == 0
    assert stats.generation == 1
    assert stats.as_dict()["expected_data_len"] == 650


def test_snapshot_reflects_changing_values(feed, stub):
    started(feed)
    stub.feed_fz = -88.75

    assert wait_for(lambda: feed.snapshot().fz == -88.75)
    assert feed.snapshot().ft_active is True


def test_tcp_z_comes_off_the_pose(feed):
    started(feed)
    assert feed.snapshot().tcp_z == 315.5


def test_fz_keeps_the_sensor_native_sign(feed, stub):
    """Negative under compression. Display code flips it; the feed must not."""
    stub.feed_fz = -120.0
    started(feed)
    assert wait_for(lambda: feed.snapshot().fz == -120.0)
    assert feed.snapshot().ft_values[2] == -120.0


# --- digital IO -----------------------------------------------------------


def test_di_bits_decode_per_channel(feed, stub):
    stub.feed_di = 0b0000_0010  # DI1 high (stud on work), DI0 low (not ready)
    started(feed)
    assert wait_for(lambda: feed.snapshot().di(1) == 1)

    snap = feed.snapshot()
    assert snap.di(0) == 0
    assert snap.di(1) == 1
    assert snap.di(7) == 0


def test_do_bits_decode_per_channel(feed, stub):
    stub.feed_do = 0b0000_0011  # DO0 weld trigger, DO1 feeder advance
    started(feed)
    assert wait_for(lambda: feed.snapshot().do(0) == 1)
    assert feed.snapshot().do(1) == 1


def test_do_cycle_counter_advances(feed, stub):
    """Bits 4-7 are the cycle counter the new tracker will consume."""
    started(feed)
    stub.feed_cycle_ms = 20

    def counter():
        packed = feed.snapshot().get("cl_dgt_output_l")
        return None if packed is None else (packed >> 4) & 0x0F

    assert wait_for(lambda: counter() == 1)
    assert wait_for(lambda: counter() == 2)


def test_di_out_of_range_is_a_programming_error(feed):
    started(feed)
    with pytest.raises(ValueError):
        feed.snapshot().di(16)


def test_accessors_return_none_on_an_empty_snapshot():
    """Before any frame arrives nothing is known — and nothing reads as zero."""
    snap = StatusFeed().snapshot()
    assert snap.program_state is None
    assert snap.fz is None
    assert snap.di(0) is None
    assert snap.is_fresh() is False


# --- freshness and recovery ----------------------------------------------


def test_snapshot_goes_stale_when_frames_stop(feed, stub):
    started(feed)
    stub.feed_on = False

    assert wait_for(lambda: not feed.snapshot().is_fresh(max_age_s=0.2))
    # The last frame is retained with an honest age, not blanked or zeroed.
    assert feed.snapshot().program_state is not None
    assert feed.snapshot().age_s() > 0.2


def test_feed_resumes_after_the_stream_pauses(feed, stub):
    started(feed)
    stub.feed_on = False
    assert wait_for(lambda: not feed.snapshot().is_fresh(max_age_s=0.2))

    stub.feed_on = True
    assert wait_for(lambda: feed.snapshot().is_fresh(max_age_s=0.5))


def test_feed_reconnects_after_the_server_drops(stub):
    server = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    server.start()
    port = server.port
    client = StatusFeed(port=port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    try:
        started(client)
        assert client.stats().generation == 1

        server.stop()
        assert wait_for(lambda: not client.snapshot().is_fresh(max_age_s=0.3))

        replacement = StatusFeedServer(stub, host="127.0.0.1", port=port, period_s=PERIOD)
        replacement.start()
        try:
            # Wait on the generation, not on freshness: the stale frame is still
            # within a longer freshness budget, so testing that first would pass
            # without a reconnect having happened at all.
            assert wait_for(lambda: client.stats().generation >= 2, timeout=20.0)
            assert wait_for(lambda: client.snapshot().is_fresh(max_age_s=0.5))
            assert client.snapshot().generation >= 2
        finally:
            replacement.stop()
    finally:
        client.stop()


def test_feed_survives_a_refused_connection_and_keeps_retrying(stub):
    """Nothing is listening — the thread must back off, not die or spin."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    client = StatusFeed(port=dead_port, connect_timeout_s=0.5, recv_timeout_s=0.2)
    try:
        client.start("127.0.0.1")
        assert wait_for(lambda: client.stats().state == FeedState.ERROR.value)
        assert wait_for(lambda: client.stats().last_error is not None)
        time.sleep(0.3)
        assert any(t.name == "robot-feed" for t in threading.enumerate())
        assert client.snapshot().is_fresh() is False
    finally:
        client.stop()


def test_stop_is_idempotent_and_leaves_no_thread(feed):
    started(feed)
    feed.stop()
    feed.stop()

    assert feed.stats().state == FeedState.STOPPED.value
    assert wait_for(lambda: not any(t.name == "robot-feed" for t in threading.enumerate()))


def test_retarget_moves_to_the_new_address(stub):
    first = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    first.start()
    client = StatusFeed(port=first.port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    try:
        started(client)
        assert client.stats().ip == "127.0.0.1"
        client.retarget("127.0.0.2")
        assert wait_for(lambda: client.stats().ip == "127.0.0.2")
    finally:
        client.stop()
        first.stop()


# --- malformed streams ----------------------------------------------------


def test_corrupt_checksums_are_counted_and_the_stream_recovers(feed, stub):
    started(feed)
    before = feed.stats().frames
    stub.feed_corrupt = 3

    assert wait_for(lambda: feed.stats().reader.get("checksum_fail", 0) >= 3)
    # Frames keep being published either side of the bad ones.
    assert wait_for(lambda: feed.stats().frames > before + 3)
    assert feed.snapshot().is_fresh()


def test_injected_garbage_is_resynced_past(feed, stub):
    started(feed)
    stub.feed_garbage = 64

    assert wait_for(lambda: feed.stats().reader.get("resync_bytes", 0) > 0)
    assert feed.snapshot().is_fresh()


def test_short_frames_still_publish_what_they_carry(stub):
    """Older firmware sending a shorter DATA must not cost us force telemetry."""
    stub.feed_data_len = 300
    server = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    server.start()
    client = StatusFeed(port=server.port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    try:
        started(client)
        snap = client.snapshot()
        assert snap.program_state is not None
        assert snap.fz is not None                # offset 179, present
        assert snap.fault_main is None            # offset 412, absent
        assert client.stats().reader["short_data"] > 0
    finally:
        client.stop()
        server.stop()


def test_big_endian_frames_are_detected_and_decoded(stub):
    """The manual never states byte order, so the feed proves it per connection."""
    stub.feed_little_endian = False
    stub.program_state = 2
    stub.feed_fz = -42.5
    server = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    server.start()
    client = StatusFeed(port=server.port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    try:
        started(client)
        snap = client.snapshot()
        assert snap.little_endian is False
        assert snap.program_state == 2
        assert snap.fz == -42.5
        assert snap.tcp_z == 315.5
    finally:
        client.stop()
        server.stop()


def test_undecodable_frames_publish_nothing_at_all(stub):
    """A layout we cannot vouch for must read as no data, never as plausible data."""
    stub.program_state = 9   # outside the documented 1-4, so looks_sane fails
    stub.feed_little_endian = False
    server = StatusFeedServer(stub, host="127.0.0.1", port=0, period_s=PERIOD)
    server.start()
    client = StatusFeed(port=server.port, connect_timeout_s=2.0, recv_timeout_s=0.2)
    try:
        client.start("127.0.0.1")
        assert wait_for(lambda: client.stats().state == FeedState.LAYOUT_ERROR.value)
        assert client.snapshot().is_fresh() is False
        assert client.snapshot().program_state is None
        assert "did not decode sanely" in (client.stats().last_error or "")
    finally:
        client.stop()
        server.stop()
