# Safety, error handling & connection state

## Connection architecture — `RobotLink` (`backend/robot_link.py`)

This section is about a WeldFlex-side pattern, not an `Robot.py` API, but it's
the thing that determines whether a given SDK call is even safe to make
concurrently with another — squarely this skill's territory. Full spec:
`docs/ROBOT_TELEMETRY.md`; wrapper-layer conventions built on top of it:
`weldflex-app` skill's `robot-service-wrapper.md`.

**One daemon thread (`_SdkWorker`) is the only thing ever allowed to call into
the SDK object**, including the heartbeat probe. This isn't a style
preference: `xmlrpc.client` cannot safely interleave request/response pairs on
one connection, so two threads racing calls against the same `Robot.RPC`
instance corrupts the stream for both. A prior design let the supervisor
thread bypass the worker for a "quick" probe when the worker was busy; that
was removed specifically because it violated this rule.

**Work is a priority queue, not a FIFO**: `0` = operator command, `1` = core
heartbeat probe, `2` = detailed telemetry. A slow detail poll (weld-test sysvar
reads, jog pose) can never starve a Run/Stop button press, and the heartbeat
that keeps `current_line`/fault data fresh outranks detail polling too. Equal
detail requests against the same connection generation coalesce onto one
in-flight `Future` via a `coalesce_key` — concurrent callers get the same
result rather than each queuing a duplicate controller round-trip.

**Every client is generation-tagged.** A call failure only invalidates the
generation it observed (`RobotLink._invalidate`), so a status poll timing out
at 5s cannot tear down the client a 30s upload is still using underneath it.
Reconnects and IP retargets bump the generation; a result that arrives after
its generation was retired is discarded rather than refreshing a newer
client's cache.

**There is a second channel, and it fails independently.** `backend/robot_feed.py`
reads the controller's port-8083 status push on its own thread, its own socket
and its own generation counter — none of the above applies to it, deliberately.
The reason is a live finding (2026-08-03): the controller **stops answering
XML-RPC for the duration of a force operation** such as `FT_FindSurface`, while
the program runs on normally and the pushed frame keeps arriving. So "XML-RPC is
dead" no longer implies "the robot is gone".

Consequences for anyone writing against this:

- `UniversalRobotState.commands_available` is the only correct gate for a
  command. `feed_streaming` says a frame arrived; it says nothing about whether
  a Run or Stop would reach the controller.
- A new connection state, `telemetry`, means feed-up/XML-RPC-down. The UI shows
  it amber as `TELEMETRY` — not `ONLINE` (Stop would silently do nothing) and
  not `OFFLINE` (the arm is visibly moving). It is expected mid-force-op and is
  not a reason to reconnect.
- An operator `disconnect` outranks a live feed. Disconnect is an intent, not a
  failure, and the feed must not paper over it.
- Anything still reading `ConnSnapshot` directly rather than
  `get_universal_state()` — the job manager's cycle tracker, notably — is still
  exposed to the XML-RPC outage and will stall through it.

**The supervisor thread owns everything that blocks for seconds**: building
`Robot.RPC()`, tearing one down (closing `FRCNDEClient` alone can spend ~3s
contending a mutex, plus thread joins). Request threads never construct or
destroy a client — they read a cached snapshot (`ConnSnapshot`, `ForceSnapshot`)
or queue work onto the one worker.

**A watchdog can abandon a stuck worker.** If a call has been active longer
than `STUCK_AFTER_S` (default 120s), the supervisor retires the wedged worker
(a plain daemon thread — abandoning it costs a thread, nothing else, since it
can't be interrupted mid-SDK-call) and spins up a replacement rather than
blocking the whole link forever. Separately, `BUSY_STALE_S` (default 45s)
governs when a link with no successful call *while busy* gets marked
`DEGRADED` even though the worker itself isn't stuck — e.g. a jog pose poll
queued for a while behind a long upload.

Practical upshot for anyone adding a new `WeldFlexRobotService` method: pass
`priority=2` for anything polled from a page (status, jog pose, weld
telemetry) and leave the default `priority=0` for anything the operator
explicitly triggers (Run, Stop, Reconnect). Use `coalesce_key` for a detail
read that multiple browser polls might trigger concurrently.

## The `xmlrpc_timeout` decorator — `Robot.py:536`

```python
def xmlrpc_timeout(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if RPC.is_connect == False:
            return -4
        else:
            result = func(self, *args, **kwargs)
            return result
    return wrapper
```

This is the connection-error convention's enforcement point: any decorated
method returns bare `-4` immediately, with no RPC attempt, whenever
`RPC.is_connect` is `False`.

**Not universal.** Several methods have this decorator commented out
(`GetDI` and a few motion-status getters, ~lines 3324/3403/4329/4360) or
omitted entirely (`GetSafetyCode`, `LuaUpload`, `LuaDelete`). Don't assume
every SDK call will short-circuit to `-4` when disconnected — some will
instead hang, error differently, or silently serve stale local-cache data.

`RPC.is_connect` is set in `RPC.__init__` (`Robot.py:2238-2308`) based on a
successful `GetControllerIP()` XML-RPC probe (CNDE/port-20005 connectivity is
optional and does *not* block `is_connect=True`), and re-set by `reconnect()`
(`Robot.py:2370`).

## Error code convention — `RobotError` class, `Robot.py:548`

| Code | Constant | Meaning |
|---|---|---|
| `0` | `ERR_SUCCESS` | Success |
| `-1` | `ERR_OTHER` | Other/unspecified error |
| `-2` | `ERR_SOCKET_COM_FAILED` | Socket communication failure |
| `-3` | `ERR_XMLRPC_COM_FAILED` | XML-RPC communication failure |
| `-4` | `ERR_RPC_ERROR` | Disconnected — returned directly by `xmlrpc_timeout` when `RPC.is_connect` is `False` |

`backend/robot_service.py`'s `_has_conn_error()` treats any of `{-4,-3,-2}`
appearing anywhere in a nested return value as fatal, and forces client
recreation on the next call — the validated, working convention to replicate
for any new call path.

## Controller RPC error code `14` — "Interface execution failed"

A **positive controller-side** code (FAIRINO errcode table, readthedocs
SDKManual/errcode.html), distinct from the negative client-side `RobotError`
codes above. Vendor resolution: "check whether the web interface reports a
fault". Two live-confirmed meanings (both 2026-07-28):

1. **A latched controller fault blocks raw RPC reads until Cleared** on the
   pendant/web UI. Every read fails with 14 and the robot looks broken from
   Python; the fix is the web UI's Clear button, not a reconnect. Also note:
   a controller-side Lua `error()` does *not* latch such a fault — only real
   controller faults (e.g. joint overspeed) do.
2. **`FT_GetForceTorqueRCS` returns 14 for the whole time a force-control move
   (`FT_FindSurface`) is executing** — the force-control task owns the sensor.
   Routine, not a fault; `GetCurrentLine` keeps working through it.

Telling them apart: (1) persists after the program stops and hits *every* raw
read; (2) is confined to FT reads while motion runs and clears itself on move
end. `weld_probe()` returns the code as `ft_err` rather than raising, and the
weld-test page renders running+14 as "sensor busy" while a latched fault still
surfaces via `fault_main` (`FT_RPC_BUSY_CODE`, `backend/app.py`).

## `GetSafetyCode(self)` — `Robot.py:2762`

**No decorators at all** — no `@log_call`, no `@xmlrpc_timeout`, no
`reconnect_flag` wait. Pure local read, runs unconditionally even while
disconnected:
```python
return 99 if (safety_stop0_state==1 or safety_stop1_state==1) else 0
```
`99` is a distinct "safety stop asserted" sentinel — **not** one of the
`RobotError` connection-failure codes above, and not itself a comm error.
Called internally as a pre-flight gate by `StartJOG`, `ProgramRun`, and
`ProgramResume` — each returns `99` in place of actually running if a safety
stop is latched (see `motion-and-jog.md` and
`program-and-file-management.md`).

## `ResetAllError(self)` — `Robot.py:5298`

No params, real RPC call, bare int. Docstring: only clears **resettable**
errors — some fault states require a physical reset, not just this call.

## `GetRobotErrorCode(self)` — `Robot.py:6143`

**This is the correct/only name.** `GetRobotErrCode` (a plausible-looking
alternate spelling) **does not exist anywhere in `Robot.py`** — calling it
raises `AttributeError`.

The **SDK method itself** is a local-cache read, always error `0` (RPC call
commented out just above it):
```python
return 0, [self.robot_state_pkg.main_code, self.robot_state_pkg.sub_code]
```
**Nothing in this app calls that method any more.** `backend/robot_service.py`'s
`diagnostics()` is now a pure `ConnSnapshot` cache read with no SDK call in
its own call stack — fault codes reach it via `robot_link.py`'s core
heartbeat (`_read_fault_codes`), which uses the same three-tier fallback as
`GetProgramState` above: CNDE struct fields (`main_code`/`sub_code`) when the
CNDE receiver is confirmed streaming, else raw XML-RPC
`client.robot.GetRobotErrorCode()`, else the dead local-cache read as a last
resort. `ConnSnapshot.fault_source` tells you which tier answered; treat
`"cache"` as "no code available", not "no fault" — a fault code of `0`
sourced from `"cache"` says nothing, because that path always reads `0`
whether or not the controller is actually faulted.

**As of 2026-08-03 that whole ladder is the fallback, not the primary.**
`robot_service.get_universal_state()` reads `main_errcode`/`sub_errcode` out of
the port-8083 push whenever a frame is fresh, and only drops to the
`ConnSnapshot` tiers above when it is not; `fault_source` reports `"8083"` in
that case. One trap the frame introduces: **it reports `0` for "no fault" where
`ConnSnapshot` uses `None`**, so a frame's `0` must be normalised to `None`
before it reaches anything that treats "a value is present" as "there is a
fault". `test_frame_fault_codes_distinguish_zero_from_absent` pins this.
