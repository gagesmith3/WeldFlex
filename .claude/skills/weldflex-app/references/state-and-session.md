# State-dict pattern, recipe data model & run lifecycle

Verified against the code 2026-07-28. Earlier revisions of this file documented
`_run_session`, `_current_job`, `_lbt_session` and a poll-driven cycle loop —
**all of that is gone**, replaced by `backend/job_manager.py`. If you are reading
a cached copy that mentions Liberty, it is out of date.

## The pattern: module dict + lock + read-modify-render

Multi-step wizard/session state is carried across stateless HTTP requests as a
module-level `dict` guarded by a `threading.Lock`. Every mutating route does
its mutation inside a fresh `with <lock>:` block, then renders a partial from
a **snapshot** taken under the same lock. `_tcp_render()` (`app.py:602`) is the
clean, factored example:

```python
def _tcp_render():
    with _tcp_lock:
        pts = set(_tcp_calib["points_recorded"])
        state = dict(_tcp_calib)
    return render_template("partials/tcp_calibrate_steps.html", points_recorded=pts, ...)
```

Every route that mutates `_tcp_calib` does its mutation inside its own
`with _tcp_lock:` block, then calls `_tcp_render()`. If you're adding a new
wizard, factor a render helper like `_tcp_render()` rather than repeating the
snapshot inline at every call site.

**When building the work-object calibration feature**, add a new
`_wobj_calib`/`_wobj_lock` pair and a `_wobj_render()` helper shaped exactly
like `_tcp_calib`/`_tcp_render()` — same points-recorded-as-set,
per-step-error-key structure. See the `fairino-sdk` skill's
`coordinate-calibration.md` for the SDK calls that go inside the mutating
routes.

## State inventory

| State | Lock | Shape | Notes |
|---|---|---|---|
| `_tcp_calib` (`app.py:94`) | `_tcp_lock` (`app.py:103`) | `points_recorded` (a `set`), `drag_point`, `drag_error`, `record_error`, `apply_error`, `applied`, `tcp_offset` | The only module-level session dict left. Working reference for the wizard pattern. |
| recipes.json | `_rec_lock` (`app.py:106`) | see below | File-backed, not a dict. Every read-modify-write goes under this lock. |
| the current job | *(inside `JobManager`)* | `JobSnapshot` | **Not** module state in `app.py`. `job = JobManager(...)` (`app.py:206`) owns it behind its own internal lock. |

**No run state lives in `app.py`.** Don't add any. `/ui/job/*` routes are thin
adapters that call a `JobManager` method and re-render from the returned
snapshot.

## Recipe data model

Storage: `backend/recipes.json`, a flat JSON array, loaded/saved via
`_recipes_load()` (`app.py:108`) / `_recipes_save()` (`app.py:123`) under
`_rec_lock`.

```json
{
  "id": "uuid4-string",
  "name": "demo",
  "studs": [{"x": 100, "y": 300}, ...],
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC",
  "times_ran": 0,
  "avg_cycle_time": null,
  "last_run": null,
  "pause_points": []
}
```

`times_ran` / `avg_cycle_time` / `last_run` **are now live** — `_on_job_finish()`
(`app.py:176`) folds each finished run into the part's lifetime stats, wired in
as `JobManager(robot, on_finish=_on_job_finish, ...)`. `times_ran` counts
*cycles*, not jobs, and `avg_cycle_time` is a running mean over every cycle the
part has ever produced.

**`pause_points` is still dead.** Written as `[]` at creation (`app.py:382`),
read by nothing — `lua_builder._stud_rows` consumes only `x` and `y`. Per-stud
operator waits do not exist yet; see `docs/ARCHITECTURE.md`. Do not build
anything that assumes this field means something.

`_recipes_load()` auto-migrates any recipe missing an `id` by assigning a fresh
UUID and re-saving. `_recipes_enrich()` (`app.py:137`) derives `studs_count` and
a human `updated_label` for display. `_parse_studs()` (`app.py:149`) /
`_preview_data()` (`app.py:165`) support a freeform textarea → SVG preview as an
alternative input mode to the coordinate-table UI.

## Run lifecycle

Owned entirely by `backend/job_manager.py`. `app.py` never advances a job.

```
POST /ui/job/load     → queued      (JobManager.load: validates gate_mode, stores studs+cycles)
POST /ui/job/start    → starting    (returns immediately; build→upload→run on a job-launch thread)
                      → running     (once ProgramRun returns and the tracker is armed)
                      → error       (any launch exception)
POST /ui/job/pause    → paused      (from running)
POST /ui/job/resume   → running     (from paused)
POST /ui/job/continue → running     (from gated — operator finished the part swap)
POST /ui/job/stop     → stopped     (from any active state)
POST /ui/job/clear    → idle        (dismiss a terminal result; refused while active)
GET  /ui/job/status   → read-only re-render. Advances nothing.
```

Plus the states the **monitor thread** drives on its own: `gated` (cycle
boundary reached in `pause` gate mode), `completed`, `stopped`, `error`, and
`interrupted` (the link died mid-run).

**`clear` is also the mode handoff.** `run_program` puts the controller into
auto (`Mode(0)`) and nothing takes it out when the program ends, so
`JobManager.clear()` calls `robot.set_manual_mode()` (`Mode(1)` — the green
indicator) after dropping the session. It runs outside `_lock`, and a failure
never undoes the clear: the manager returns an otherwise-idle snapshot carrying
`error="Job cleared, but the robot stayed in auto mode: …"`, which
`partials/current_job.html` renders through its normal `job.error` slot. Like
any note in that panel, it survives only until the next `/ui/job/status` poll
(~3 s) — the durable record is the `clear` event and a `log.warning`.

**Architectural note — the inverse of the old design.** Cycle advancement is
*not* poll-driven. `JobManager._monitor_loop` runs at 250 ms on its own daemon
thread and reads only the link's cached snapshot, so **a job keeps advancing
with the kiosk tab closed**. `GET /ui/job/status` is a pure read. The old
`/ui/operator/current-job` handler that advanced cycles as a side effect of
being polled is gone; don't reintroduce that shape.

Two concurrency rules the manager depends on, which you must preserve if you
touch it:

1. **No robot I/O and no file I/O under `_lock`.** An SDK call can block for
   seconds and the lock is on the path of every status poll. The monitor
   computes transitions under the lock and returns *deferred actions* the caller
   runs after releasing it.
2. **Every mutation is `run_id`-checked.** A stale thread from a finished job
   must not be able to touch the next job's session.

## Cycle counting

The generated program's line numbers are the signal. `lua_builder` returns
`loop_start_line` and `cycle_marker_line` for the text it just produced, and
`CycleTracker` banks a cycle on **one** signal:

- **boundary dwell** — a sample at or past `cycle_marker_line`. The marker is a
  `WaitMs(BOUNDARY_MS)` long enough that a 250 ms poll cannot step over it.

A later sample *below* the marker but at or above `loop_start_line` re-arms the
counter for the next cycle. That re-arm is level-triggered, not edge-triggered,
because the wrap itself normally happens while the job is `gated` and nothing is
sampling at all.

Two things this must not go back to doing — both were real bugs, fixed 2026-07-28:

- **Do not key the re-arm on seeing `loop_start_line`.** That's the `for
  cycleIndex` statement, the lowest-numbered line in the loop, executing in
  microseconds against a 250 ms sampler. It is never sampled. Keying on it
  latched the counter after cycle 1, so `cycles_done` stuck at 1 and the
  inter-cycle gate never fired again — the robot ran the rest of its cycles with
  nobody swapping parts.
- **Do not treat "the reported line went backwards" as a cycle.** The inner stud
  loop walks the body once per stud, so body lines are non-monotonic *within* one
  cycle. A backwards jump cannot distinguish an inner iteration from an outer one.

The cost is that missing every sample across the whole dwell stalls the count.
That's why `BOUNDARY_MS` is 1500 ms, and 3000 ms in `pause` gate mode where a
host-issued `ProgramPause` also has to land inside the window. A zero-stud part
has no executable line below the marker and so cannot re-arm — a config error the
builder already flags.

Repeated identical samples are ignored via the link's `line_edge_seq`, which only
advances when the reported line changes.

**Known blocker for the weld.lua hookup**: `GetCurrentLine` reports the
*sub-file's* line numbers while a `NewDofile`'d chunk executes (live
2026-07-28 — line 262 was weld.lua's `searchForStud` with the weld-test
harness loaded), with nothing distinguishing which file a number belongs to.
Once `WeldFlex.lua`'s loop calls weld.lua, weld.lua's lines (~400 of them)
overlap the parent's `loop_start_line`/`cycle_marker_line` and the detector
above will bank phantom cycles. The counting scheme has to change — e.g. keep
the marker line strictly above every line weld.lua can report, or gate
sampling on which file is active — before that hookup ships.

Testing: `tests/test_cycle_tracker.py` drives the detector directly, and
`tools/stub_robot.py --cycle LOOP_START:MARKER:CYCLES` scripts a `GetCurrentLine`
feed without a robot. Both deliberately omit the loop head from their sequences —
if you make either one emit it, you are testing a controller that doesn't exist.

## Persistence

| File | Written by | Shape |
|---|---|---|
| `backend/run_history.jsonl` | `JobManager._finish` | one record per finished run |
| `backend/run_events.jsonl` | `JobManager._event` | load/start/cycle/gated/stop/… audit trail, rotated at 512 KB |

JSONL, not a JSON array: append is O(1), there's no read-modify-write of the
whole file, and a power cut mid-write costs one line instead of the lot. A
malformed line is skipped on read, not fatal.
