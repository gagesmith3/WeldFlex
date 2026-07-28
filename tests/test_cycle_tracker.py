"""The cycle detector, against synthetic `GetCurrentLine` sequences.

This is the one piece of the manager that cannot be reasoned about without a line
feed, so it is tested in isolation from threads, sockets and the clock.

Line layout used throughout mirrors a real build: loop head at 37, body 38-43,
boundary dwell (the marker) at 45, gate at 46, `end` at 47.
"""

import pytest

from job_manager import CycleTracker

LOOP_START = 37
MARKER = 45
GATE = 46
END = 47


def feed(tracker, lines, with_edges=True):
    """Push a line sequence through, returning how many cycles were banked."""
    banked = 0
    for i, line in enumerate(lines):
        if tracker.observe(line, edge_seq=i if with_edges else None):
            banked += 1
    return banked


def test_normal_run_counts_each_cycle_once():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    seq = []
    for _ in range(3):
        seq += [LOOP_START, 38, 40, 43, MARKER, GATE, END]
    assert feed(t, seq) == 3
    assert t.cycles_done == 3


def test_dwell_is_sampled_repeatedly_without_double_counting():
    """The marker is a 1500 ms WaitMs polled every 250 ms — ~6 identical samples."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    seq = [LOOP_START, 40] + [MARKER] * 6 + [LOOP_START, 40] + [MARKER] * 6
    assert feed(t, seq) == 2


def test_repeated_identical_sample_is_ignored_via_edge_seq():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=5)
    t.observe(LOOP_START, edge_seq=1)
    assert t.observe(MARKER, edge_seq=2) is True
    # Same edge_seq — the link has not seen a new line, so neither have we.
    assert t.observe(MARKER, edge_seq=2) is False
    assert t.observe(GATE, edge_seq=2) is False
    assert t.cycles_done == 1


def test_poll_that_skips_the_marker_still_counts_on_the_wrap():
    """The tail of the cycle is never sampled; only the backwards jump is seen."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    seq = [LOOP_START, 40, 43, LOOP_START, 41, 43, LOOP_START, 42]
    assert feed(t, seq) == 2  # two wraps observed
    assert t.cycles_done == 2


def test_marker_and_wrap_do_not_double_count_the_same_cycle():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=4)
    seq = [LOOP_START, 40, MARKER, END, LOOP_START, 40, MARKER, END, LOOP_START]
    assert feed(t, seq) == 2
    assert t.cycles_done == 2


def test_count_is_clamped_at_target():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    seq = []
    for _ in range(5):
        seq += [LOOP_START, 40, MARKER, END]
    feed(t, seq)
    assert t.cycles_done == 2


def test_backward_jump_that_is_not_a_wrap_does_not_count():
    """Lines moving around inside the body are not cycle boundaries."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    assert feed(t, [38, 42, 39, 43, 40]) == 0
    assert t.cycles_done == 0


def test_none_and_non_int_lines_are_ignored():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    for value in (None, "45", 45.0, True):
        assert t.observe(value, edge_seq=None) is False
    assert t.cycles_done == 0


def test_untargeted_tracker_counts_without_clamping():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=0)
    seq = []
    for _ in range(4):
        seq += [LOOP_START, MARKER]
    assert feed(t, seq) == 4
    assert t.cycles_done == 4


@pytest.mark.parametrize("first_line", [LOOP_START, 38, MARKER])
def test_entering_the_loop_is_not_itself_a_wrap(first_line):
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    banked = t.observe(first_line, edge_seq=1)
    assert banked is (first_line >= MARKER)
