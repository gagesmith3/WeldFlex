# Handoff: Weld Test Page Live Telemetry (2026-07-29)

## What was wrong

The `/operator/weld-test` page showed stale or blank telemetry (force sensor,
program state, current line, fault codes) while a weld test program was running.
Three distinct bugs caused this:

### Bug 1 — Supervisor skipped the heartbeat probe while the worker was busy

**File:** `backend/robot_link.py`, `_tick()`

The supervisor loop has a single SDK worker thread. Previously, while any
command was in flight (e.g. the program running), the loop returned early
without probing. That meant the cached `ConnSnapshot` never updated, so `current_line`,
`program_state_raw`, and fault fields stayed frozen.

**Fix:** Added `_fast_heartbeat` flag (set by `set_heartbeat_hint(True)` when a
program starts). The busy-skip guard became:
```python
if self._worker.is_busy() and not self._fast_heartbeat:
    ...
    return
```
When `_fast_heartbeat` is on, the supervisor still calls `_probe_body()` — but
runs it **directly on the supervisor thread** instead of submitting to the worker,
so it doesn't queue behind the running command:
```python
def _run_probe(self, client):
    if self._fast_heartbeat and self._worker.is_busy():
        return self._probe_body(client)   # supervisor thread bypass
    ...
```

### Bug 2 — Weld telemetry endpoint read from stale snapshot, not live

**File:** `backend/robot_service.py` (`weld_probe()`), `backend/app.py`
(`ui_weld_test_telemetry()`)

`weld_probe()` already batched FT, DI, sysvar, and TCP-pose reads into one
worker dispatch. It did **not** also fetch program state, current line, or
fault codes — those were only read from the snapshot, which Bug 1 left stale.

**Fix:** Extended the `_probe` inner function in `weld_probe()` to also call:
```python
state_resp = r.robot.GetProgramState()
line_resp   = r.robot.GetCurrentLine()
fault_resp  = r.robot.GetRobotErrorCode()
```
All three are best-effort (exceptions suppressed, `None` on failure). Unpacked
results added to the return dict: `program_state_raw`, `line`, `fault_main`,
`fault_sub`.

In `ui_weld_test_telemetry()`, after `weld_probe()` returns, the page now
overrides its snapshot-sourced values with the live probe values:
```python
if probe.get("program_state_raw") is not None:
    program_state = ROBOT_STATE_MAP.get(int(probe["program_state_raw"]), "unknown")
    running = program_state == "running"
if probe.get("line") is not None:
    current_line = probe["line"]
if probe.get("fault_main") is not None:
    fault_main = probe["fault_main"]
if probe.get("fault_sub") is not None:
    fault_sub = probe["fault_sub"]
```

`weld_probe()` also uses `retries=1, timeout=1.0` (not the default 3 retries /
3 s timeout) so a slow or failing probe doesn't stall the 400 ms poll loop.

### Bug 3 — Force sensor not activated before runs

**File:** `backend/app.py`, `ui_weld_test_run()`

The force sensor (`FT_Activate`) requires an explicit setup sequence after any
controller reboot or fault-clear. Without it, every `FT_GetForceTorqueRCS` call
returns a nonzero error code and `fz` shows `—` on the page. `weld.lua`'s
`FT_FindSurface` also faults immediately without an active sensor.

Previously `ft_setup()` was only callable via the manual `/ui/ft/setup` button.
Nothing called it automatically before a run.

**Fix:** Added `robot.ft_setup()` to the run launch sequence, before
`joint_overspeed_protect()` and `run_program()`:
```python
# Activate the FT sensor before every run so the readout is live from
# the moment the program starts. A controller reboot or fault-clear
# deactivates it, and weld.lua's FindSurface will fault without it.
robot.ft_setup()
```

`ft_setup()` is a ~10-second blocking sequence (FT_SetConfig → FT_SetRCS →
FT_Activate → FT_SetZero). This means the Run button on the weld test page will
appear to pause for ~10 s before the program starts. This is expected and the
CSS already has a comment about it (`operator.css:3766`).

---

## Files changed

| File | What changed |
|---|---|
| `backend/robot_link.py` | `_tick()` busy-skip guard checks `_fast_heartbeat`; `_run_probe()` bypasses worker when busy+fast |
| `backend/robot_service.py` | `weld_probe()` inner `_probe` function fetches `GetProgramState`, `GetCurrentLine`, `GetRobotErrorCode`; return dict extended with 4 new keys; `_call` uses `retries=1, timeout=1.0` |
| `backend/app.py` | `ui_weld_test_telemetry()` consumes live probe values for state/line/fault; `ui_weld_test_run()` calls `robot.ft_setup()` before run |

---

## Tests

Two new tests in `tests/test_robot_link.py`:

- `test_weld_probe_uses_short_timeout_for_live_telemetry` — verifies `_call` is
  invoked with `timeout=1.0, retries=1`
- `test_tick_still_probes_when_fast_heartbeat_is_on_and_worker_is_busy` —
  verifies the probe fires when the worker is busy and `_fast_heartbeat` is True

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_robot_link.py`

---

## Known remaining issues

- **Robot fault during FT_FindSurface:** A live test on 2026-07-29 hit an
  axis/collision fault during `FT_FindSurface`'s descent. Likely the same
  axis-2 joint-overspeed pattern seen on 2026-07-28. Not investigated yet.
  Check `fault_main`/`fault_sub` from the probe and the `_WELD_GUARD_CODES` /
  `_WELD_FAULT_SITES` tables in `app.py` when ready to investigate.

- **ft_setup() 10 s delay:** If the startup delay becomes unacceptable, a fast
  path could skip `FT_SetConfig`/`FT_SetRCS` (which persist across reboots) and
  only call `FT_Activate` + `FT_SetZero`. Not implemented.

- **CNDE stream never connects:** `robot_state_pkg` is always zero on this
  firmware. All reads in `weld_probe()` and `_probe_body()` use
  `client.robot.<Method>()` bypasses — not the SDK wrappers — for this reason.
  Do not switch them to SDK wrappers without testing on hardware first.

- **`pause_points` field is dead.** Every recipe carries it; nothing reads it.
  Per-stud operator waits do not exist. Do not implement unless the owner asks.

- **Nothing welds end-to-end yet.** `WeldFlex.lua`'s cycle loop still does not
  call `weld.lua`. The weld test page (`/operator/weld-test`) is the only place
  `weld.lua` runs, and only when `WELD_ARMED = 1`.
