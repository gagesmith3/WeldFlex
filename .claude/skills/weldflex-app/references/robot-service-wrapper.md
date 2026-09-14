# `robot_service.py` — the SDK wrapper layer

`WeldFlexRobotService` wraps `Robot.RPC` behind a single-worker
`ThreadPoolExecutor` so every SDK call gets a hard timeout even though the raw
SDK can block indefinitely. **Never call `Robot.py` methods directly from
`app.py`** — always go through a `WeldFlexRobotService` method, and any new
method should follow the standard pattern below.

## `_call()` / `_unpack()` / `_has_conn_error()`

> [!IMPORTANT]
> **`_call` no longer owns the connection.** It is now a thin forwarder to
> `RobotLink.call()`, which owns client construction, teardown, retries and the
> worker thread. `WeldFlexRobotService` has no `self._robot`, no `self._executor`
> and no `_close_client`. Anything below that describes this file managing an
> `Robot.RPC` handle itself is describing a structure that no longer exists;
> `backend/robot_link.py` and the `fairino-sdk` skill's
> `error-handling-and-connection.md` are the current references for the
> lifecycle.

```python
def _call(self, fn, timeout=SDK_TIMEOUT_S, retries=3,
          priority=0, coalesce_key=None, rpc_timeout=None):
    """Run an SDK call on the link's worker thread with a hard timeout."""
    return self._link.call(
        fn, timeout=timeout, retries=retries,
        label=getattr(fn, "__name__", "call"),
        priority=priority, coalesce_key=coalesce_key, rpc_timeout=rpc_timeout,
    )
```

The four keyword arguments beyond `timeout`/`retries` are the ones worth
knowing:

- **`priority`** — operator command (`0`) beats core heartbeat (`1`) beats
  detailed telemetry (`2`) on the link's single worker.
- **`coalesce_key`** — equal requests sharing a connection generation share one
  worker future instead of queueing duplicate controller reads. Key per *thing
  read*, not per caller (`weld_probe` uses `f"weld-sysvar:{slot}"`).
- **`rpc_timeout`** — narrows the XML-RPC transport's socket timeout for this
  one call, then restores it. `timeout` bounds how long *you* wait; `rpc_timeout`
  bounds how long the **worker** is occupied, which is what actually delays the
  next operator command. Telemetry reads pass it (`TELEMETRY_RPC_TIMEOUT_S`,
  0.5 s); commands should not.
- A call whose caller has already timed out is dropped before it executes rather
  than run against a worker nobody is waiting on.

- **`_unpack(response)`** normalizes the SDK's inconsistent return shapes —
  `(err_code, value)` tuple, `(err_code,)` singleton, bare int, or opaque
  non-numeric — into `(int, Any)`. See the `fairino-sdk` skill's
  `error-handling-and-connection.md` for *why* the SDK's shapes vary this much;
  this helper is what absorbs that variance so callers don't have to.
- **`_has_conn_error(val)`** recursively checks for SDK codes `-4`/`-3`/`-2`
  anywhere in a nested result. It and `_is_conn_code` are now `staticmethod`
  re-exports of `robot_link.has_conn_error` / `is_conn_code`, so the link's own
  dispatch path and the wrapper layer cannot drift apart.

## Standard method pattern

Every public method on `WeldFlexRobotService` should follow this shape:

```python
def some_action(self, ...) -> ReturnType:
    resp = self._call(lambda r: r.SomeFn(...))
    err_code, value = self._unpack(resp)
    if err_code != 0:
        raise RuntimeError(f"SomeFn failed (code {err_code})")
    return value
```

Concrete examples: `pause_program()`, `tcp_compute_and_apply()`, `jog_step()`. This is the pattern to copy for any new
SDK-backed method — including the work-object calibration methods
(`wobj_enable_drag`/`wobj_record_point`/`wobj_compute_and_apply`, mirroring
`tcp_enable_drag`/`tcp_record_point`/`tcp_compute_and_apply`).

## Named deviations

- **`status()`/`diagnostics()` do no robot I/O at all.** They read
  `self._link.snapshot()` and shape it. Polled once a second by every open page,
  so this is the point: the controller is contacted once per heartbeat by the
  supervisor regardless of how many browsers are watching. The same now holds for
  `get_universal_state()`, `ft_read()`, `force_snapshot()` and `feed_snapshot()`
  — see gotcha 15 in the SKILL. Any new polled route must follow them; a route
  that calls `_call()` per poll multiplies robot traffic by the number of open
  tabs.
- **`reconnect()`** is one line — `self._link.request_reconnect()`, which asks
  the supervisor thread to cycle the client. It does **not** build or tear down
  anything on the calling thread. Surfaced by `/ui/diagnostics/reconnect`.
- **`jog_step()`** is the one method that
  combines a command call with a blocking wait loop instead of returning
  immediately: it calls `StartJOG`, then polls `GetRobotMotionDone()` (with
  `retries=1`) up to `JOG_MOTION_TIMEOUT_S=5.0` at `JOG_MOTION_POLL_S=0.02s`
  intervals, after an initial `JOG_MOTION_SETTLE_S=0.05s` sleep to avoid
  reading a stale "already done" state right after issuing the command. See
  the `fairino-sdk` skill's `motion-and-jog.md` for why this polling approach
  beats the bundled examples' fixed-sleep approach.

## The jog ref-mapping table

```python
# robot_service.py, above the class
JOG_START_REF = {
    ("cartesian", "base"): 2,
    ("cartesian", "tool"): 4,
    ("cartesian", "workpiece"): 8,
}
JOG_AXIS_NB = {"x": 1, "y": 2, "z": 3, "rx": 4, "ry": 5, "rz": 6}
JOG_DIRECTION = {"negative": 0, "positive": 1}
```
A documented SDK-quirk lookup table — jog stop-ref is always start-ref + 1
(not encoded here since `jog_stop()` uses `ImmStopJOG()`, which needs no ref
at all). Preserve this table verbatim if you ever need to extend jog to
support joint mode (`ref=0`) — don't re-derive the ref numbers from memory.

## Cross-reference: `GetProgramState`'s undocumented state 4

The SDK's `GetProgramState` can return `4` (drag-teach mode active), a value
its own docstring never mentions (see the `fairino-sdk` skill's
`program-and-file-management.md`). **`STATE_MAP` now handles it** — it maps
`-1` → `"offline"`, `0`/`1` → `"stopped"`, `2` → `"running"`, `3` → `"paused"`
and `4` → `"drag"`, so a robot left in drag-teach reads as `drag` rather than
`unknown`. Earlier revisions of this file said it fell through to `"unknown"`
and was "not currently fixed"; that is no longer true. `frame_8083.PROGRAM_STATES`
carries the same `1`–`4` mapping for the pushed feed, and the two must agree — the
telemetry cutover relies on it being a change of source, not of meaning.
