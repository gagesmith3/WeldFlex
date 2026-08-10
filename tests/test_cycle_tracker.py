"""The cycle detector, against synthetic `GetCurrentLine` sequences.

This is the one piece of the manager that cannot be reasoned about without a line
feed, so it is tested in isolation from threads, sockets and the clock.

Line layout used throughout mirrors a real build: loop head at 37, body 38-43,
boundary dwell (the marker) at 45, gate at 46, `end` at 47.

**These sequences deliberately never contain LOOP_START.** That is the `for
cycleIndex` statement — the lowest-numbered line in the loop, executing in
microseconds against a 250 ms sampler. The controller does not report it in
practice, and an earlier version of this file assumed it did, which is exactly
how a latched counter shipped: cycle 1 banked, nothing after it ever did, and
the inter-cycle gate stopped firing. Body lines are what a real feed looks like.

Body lines are also *not* monotonic within a cycle: the inner stud loop runs
38-43 once per stud. Sequences here repeat body lines to keep that honest.
"""

import pytest

from job_manager import CycleTracker

LOOP_START = 37
BODY = 38
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
        # Two studs, so the body is walked twice, then the boundary dwell.
        seq += [BODY, 40, 43, BODY, 40, 43, MARKER, GATE, END]
    assert feed(t, seq) == 3
    assert t.cycles_done == 3


def test_second_cycle_is_counted_when_the_loop_head_is_never_sampled():
    """The 2026-07-28 regression: cycles_done stuck at 1 and the gate never re-armed.

    Not one sample here is at or below LOOP_START, because the real controller
    never lands one there.
    """
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    seq = []
    for _ in range(3):
        seq += [BODY, 41, 43, MARKER, MARKER, GATE]
    assert min(seq) > LOOP_START, "a real feed never reports the loop head"
    assert feed(t, seq) == 3
    assert t.cycles_done == 3


def test_a_gate_hold_between_cycles_does_not_lose_the_reset():
    """Nothing feeds the tracker while the job is GATED, so the wrap is not observed.

    The re-arm has to be level-triggered on the samples that come back *after* the
    operator presses Continue, not edge-triggered on the wrap itself.
    """
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    assert feed(t, [BODY, 43, MARKER]) == 1          # cycle 1 banks, then we gate

    # ... blackout: the wrap into cycle 2 happens here, unsampled ...

    assert feed(t, [BODY, 41, 43, MARKER]) == 1      # continue; cycle 2 still banks
    assert t.cycles_done == 2


def test_dwell_is_sampled_repeatedly_without_double_counting():
    """The marker is a long WaitMs polled every 250 ms — several identical samples."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    seq = [BODY, 40] + [MARKER] * 6 + [BODY, 40] + [MARKER] * 6
    assert feed(t, seq) == 2


def test_repeated_identical_sample_is_ignored_via_edge_seq():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=5)
    t.observe(BODY, edge_seq=1)
    assert t.observe(MARKER, edge_seq=2) is True
    # Same edge_seq — the link has not seen a new line, so neither have we.
    assert t.observe(MARKER, edge_seq=2) is False
    assert t.observe(GATE, edge_seq=2) is False
    assert t.cycles_done == 1


def test_missing_the_boundary_dwell_stalls_the_count():
    """The dwell is the only signal that banks a cycle. Miss it and nothing counts.

    The deliberate trade for dropping the old "jumped back to the loop head"
    fallback, which could never fire on real hardware. Pinned by a test so it
    cannot change by accident: a backwards jump is NOT a cycle, because the inner
    stud loop makes one on every stud.
    """
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    seq = [BODY, 40, 43, BODY, 41, 43, BODY, 42]     # three wraps, marker never seen
    assert feed(t, seq) == 0
    assert t.cycles_done == 0


def test_marker_is_banked_once_per_cycle():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=4)
    seq = [BODY, 40, MARKER, END, BODY, 40, MARKER, END, BODY]
    assert feed(t, seq) == 2
    assert t.cycles_done == 2


def test_count_is_clamped_at_target():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    seq = []
    for _ in range(5):
        seq += [BODY, 40, MARKER, END]
    feed(t, seq)
    assert t.cycles_done == 2


def test_backward_jump_that_is_not_a_wrap_does_not_count():
    """Lines moving around inside the body are the inner stud loop, not a boundary."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    assert feed(t, [38, 42, 39, 43, 40]) == 0
    assert t.cycles_done == 0


def test_a_line_outside_the_loop_does_not_re_arm_the_counter():
    """A header line or a stray 0 must not be mistaken for the start of a new cycle."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    assert feed(t, [BODY, MARKER]) == 1
    # Below loop_start: not inside the loop, so it cannot mean "next cycle".
    assert feed(t, [0, 12, MARKER]) == 0
    assert t.cycles_done == 1


def test_none_and_non_int_lines_are_ignored():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    for value in (None, "45", 45.0, True):
        assert t.observe(value, edge_seq=None) is False
    assert t.cycles_done == 0


def test_untargeted_tracker_counts_without_clamping():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=0)
    seq = []
    for _ in range(4):
        seq += [BODY, MARKER]
    assert feed(t, seq) == 4
    assert t.cycles_done == 4


@pytest.mark.parametrize("first_line", [LOOP_START, BODY, MARKER])
def test_entering_the_loop_is_not_itself_a_wrap(first_line):
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2)
    banked = t.observe(first_line, edge_seq=1)
    assert banked is (first_line >= MARKER)


# --- NewDofile line aliasing (2026-08-06 live bug on weld_faceplate.lua) ---
#
# GetCurrentLine reports weld.lua's *own* line numbers while it runs under
# NewDofile — weld.lua is ~500 lines, dwarfing MARKER (45). Without a ceiling,
# any sample taken while inside weld.lua looks like "past the boundary" and
# banks a cycle seconds before the real dwell is reached.
WELD_LUA_LINE = 350        # a plausible in-progress sample from inside weld.lua
PROGRAM_MAX_LINE = END     # the caller program's own real length


def test_a_sample_from_inside_newdofile_does_not_bank_early():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3, program_max_line=PROGRAM_MAX_LINE)
    # The weld is still running inside weld.lua (aliased high line numbers),
    # nowhere near the real boundary dwell yet.
    assert feed(t, [BODY, WELD_LUA_LINE, WELD_LUA_LINE, 495]) == 0
    assert t.cycles_done == 0
    # It still banks once execution actually returns to the caller and reaches
    # the real marker.
    assert feed(t, [BODY, MARKER]) == 1
    assert t.cycles_done == 1


def test_full_cycle_with_newdofile_noise_only_banks_at_the_real_marker():
    t = CycleTracker(LOOP_START, MARKER, cycles_target=2, program_max_line=PROGRAM_MAX_LINE)
    seq = []
    for _ in range(2):
        # weld.lua runs per stud, reporting its own (aliased) line numbers,
        # before control returns to the caller and reaches the real boundary.
        seq += [BODY, WELD_LUA_LINE, 420, 495, BODY, MARKER, GATE, END]
    assert feed(t, seq) == 2
    assert t.cycles_done == 2


def test_without_a_ceiling_the_alias_bug_reproduces():
    """Pins the bug this fix closes: no `program_max_line` means a weld.lua-sized
    line number is indistinguishable from a real marker sample and banks early."""
    t = CycleTracker(LOOP_START, MARKER, cycles_target=3)
    assert feed(t, [BODY, WELD_LUA_LINE]) == 1
    assert t.cycles_done == 1
