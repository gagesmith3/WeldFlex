# Jog & manual motion

## `StartJOG(self, ref, nb, dir, max_dis, vel=20.0, acc=100.0)` — `Robot.py:2922`

Starts a bounded single jog "tap." Returns a bare int error code.

| Param | Meaning |
|---|---|
| `ref` | `0`=joint jog, `2`=base-coord jog, `4`=tool-coord jog, `8`=workpiece-coord jog |
| `nb` | `1`–`6` = joint1–6 (or x/y/z/rx/ry/rz, depending on `ref`) |
| `dir` | `0`=negative, `1`=positive |
| `max_dis` | Max angle (°) or distance (mm) this single jog tap will travel |
| `vel` / `acc` | Percent, default `20`/`100` |

**Gotcha**: before doing anything else, `StartJOG` calls `self.GetSafetyCode()`
(`Robot.py:2925`) and if it's non-zero, returns *that* code instead of
proceeding. A "StartJOG failed (code 99)" means a safety stop is latched — not
an SDK/comm error. See `error-handling-and-connection.md` for `GetSafetyCode`.

## `StopJOG(self, ref)` — `Robot.py:2950`

Decelerated stop. **`ref` = start-ref + 1**: `1`/`3`/`5`/`9` for joint/base/
tool/workpiece stop (confirmed against both the Chinese docstring and the
English PDF §2.4.4.2). Passing the *start* ref value here is a no-op/wrong
target, not an error you'll be warned about.

## `ImmStopJOG(self)` — `Robot.py:2971`

No params. Immediate stop (vs. `StopJOG`'s decelerated stop) — halts whichever
jog mode is currently active, regardless of what `ref` started it. Use this
when you don't know (or don't want to track) which mode was jogging.

## `GetRobotMotionDone(self)` — `Robot.py:6129`

**Not an RPC round-trip.** Body is just:
```python
return 0, self.robot_state_pkg.motion_done
```
Always returns error `0` (assuming connected) and reads the locally-cached UDP/
CNDE state packet — no network call. `motion_done`: `0`=not done, `1`=done.
Still wrapped in `@xmlrpc_timeout`, so it returns bare `-4` if `RPC.is_connect`
is `False`, before the body even runs.

## Polling vs. sleeping

The bundled examples (`windows/example/TestMotionCommand.py:24-42`,
`windows/example/NewTest0609.py:62-88`) call `StartJOG` then `time.sleep(1-3)`
before calling `StopJOG`/`ImmStopJOG` — a fixed-delay approach.

`backend/robot_service.py`'s `jog_step()` instead **polls**
`GetRobotMotionDone()` after a short settle: sleep `0.05s`, then poll every
`0.02s` up to a `5.0s` timeout, returning as soon as `motion_done == 1`. This
is more robust than a fixed sleep (adapts to actual step distance/velocity)
and is the validated, live-tested pattern — see the `weldflex-app` skill's
`robot-service-wrapper.md` for the actual implementation; this file only
documents the underlying SDK calls it's built from.
