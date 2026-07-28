# `robot_service.py` — the SDK wrapper layer

`WeldFlexRobotService` wraps `Robot.RPC` behind a single-worker
`ThreadPoolExecutor` so every SDK call gets a hard timeout even though the raw
SDK can block indefinitely. **Never call `Robot.py` methods directly from
`app.py`** — always go through a `WeldFlexRobotService` method, and any new
method should follow the standard pattern below.

## `_call()` / `_unpack()` / `_has_conn_error()`

```python
SDK_TIMEOUT_S = 5.0   # robot_service.py:56

def _call(self, fn, timeout=SDK_TIMEOUT_S, retries=3):
    for attempt in range(retries):
        try:
            with self._lock:
                if self._robot is None:
                    self._robot = Robot.RPC(self.robot_ip)
                client = self._robot
            future = self._executor.submit(lambda: fn(client))
            result = future.result(timeout=timeout)
            if self._has_conn_error(result):
                raise RuntimeError("SDK reported communication failure")
            return result
        except Exception as e:
            with self._lock:
                if self._robot is not None:
                    self._close_client(self._robot)
                    self._robot = None            # force reconnect next attempt
            if attempt == retries - 1:
                if isinstance(e, concurrent.futures.TimeoutError):
                    raise RuntimeError(f"Robot did not respond within {int(timeout)}s — connection may be lost.")
                raise e
            time.sleep(0.1)
```
(`robot_service.py:111-135`) — acquires `self._lock`, lazily creates
`Robot.RPC(self.robot_ip)` if needed, submits the call to the executor with a
hard `timeout`, checks the result for connection-error codes, and on *any*
exception closes+nulls the client (forcing a fresh `Robot.RPC` on the next
attempt) before retrying up to `retries` times with a `0.1s` backoff.

- **`_unpack(response)`** (`robot_service.py:150-160`) normalizes the SDK's
  inconsistent return shapes — `(err_code, value)` tuple, `(err_code,)`
  singleton, bare int, or opaque non-numeric — into `(int, Any)`. See the
  `fairino-sdk` skill's `error-handling-and-connection.md` for *why* the SDK's
  shapes vary this much; this helper is what absorbs that variance so callers
  don't have to.
- **`_has_conn_error(val)`** (`robot_service.py:137-148`) recursively checks
  for SDK codes `-4`/`-3`/`-2` anywhere in a nested result and triggers client
  recreation + retry.

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

Concrete examples: `pause_program()` (`robot_service.py:162-166`),
`tcp_compute_and_apply()` (`robot_service.py:369-379`), `jog_step()`
(`robot_service.py:315-326`). This is the pattern to copy for any new
SDK-backed method — including the work-object calibration methods
(`wobj_enable_drag`/`wobj_record_point`/`wobj_compute_and_apply`, mirroring
`tcp_enable_drag`/`tcp_record_point`/`tcp_compute_and_apply`).

## Named deviations

- **`status()`/`diagnostics()`** (`robot_service.py:427-497`) use `retries=1`
  instead of the default 3 — since they're polled every ~1s, a slow retry loop
  would stack up requests. They also manually force-close the client only when
  `-4` comes back from *both* underlying calls simultaneously, rather than
  relying on `_has_conn_error` alone — a single `-4` from one call isn't
  necessarily fatal (e.g. `GetCurrentLine` can transiently `-4` while
  `GetProgramState` succeeds).
- **`reconnect()`** (`robot_service.py:505-511`) is the manual "force a new
  SDK client" escape hatch, surfaced by `/ui/diagnostics/reconnect`.
- **`jog_step()`** (`robot_service.py:315-335`) is the one method that
  combines a command call with a blocking wait loop instead of returning
  immediately: it calls `StartJOG`, then polls `GetRobotMotionDone()` (with
  `retries=1`) up to `JOG_MOTION_TIMEOUT_S=5.0` at `JOG_MOTION_POLL_S=0.02s`
  intervals, after an initial `JOG_MOTION_SETTLE_S=0.05s` sleep to avoid
  reading a stale "already done" state right after issuing the command. See
  the `fairino-sdk` skill's `motion-and-jog.md` for why this polling approach
  beats the bundled examples' fixed-sleep approach.

## The jog ref-mapping table

```python
# robot_service.py:67-73
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
`program-and-file-management.md`). `robot_service.py`'s `STATE_MAP`
(`robot_service.py:58`) only maps `0`/`1`/`2`/`3` → a read of `4` falls
through to `"unknown"`. This matters if a future feature (e.g. work-object
drag-teach) leaves the robot in drag mode while something else polls
`status()`/`diagnostics()` — the UI will show "unknown" rather than something
meaningful. Not currently fixed; see `../../sdk-alignment-findings.md`.
