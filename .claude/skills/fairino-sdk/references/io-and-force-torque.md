# IO & force-torque sensor

## The machine's IO map — what each line physically is

**This is wiring, not an SDK fact.** Nothing in the SDK, the controller, or any
error message will tell you a number is wrong; a swapped line just interlocks on
the wrong signal and still "works".

| Line | Meaning | Read/written by |
|---|---|---|
| **DI1** | **Stud on work** — continuity through welder → work surface → gun. This is what turns "we touched something" into "the stud is seated". | `weld.lua` `DI_STUD_ON_WORK`; `app.py` `WELD_STUD_DI` |
| **DI0** | **Ready / caps at charge** — the welder itself is able to fire. Drops after every shot, returns when the bank recovers. | `weld.lua` `DI_WELD_READY`; `app.py` `WELD_READY_DI` |
| **DO0** | Weld trigger (250 ms pulse) | `weld.lua` `DO_WELD` |
| **DO1** | Stud feeder advance (1 s pulse) | `weld.lua` `DO_FEED`, and `feedCycle.lua` |

Corrected 2026-07-28 — DI0/DI1 were previously implemented the other way round,
with DI0 documented as "stud on work" and the ready line not monitored at all.
Audit log has the full entry.

Two consequences worth carrying:

- **The numbers live in two places**, `programs/weld.lua` and `backend/app.py`,
  because a Lua constant cannot be imported into Flask. Drift between them is
  silent — the page would report one input while the program gated on the other
  — so `tests/test_lua_builder.py` parses both files and asserts they agree.
  Change them together and let the test catch you.
- **DI0 is waited on, not sampled.** `weld.lua`'s `waitForWeldReady()` polls it
  with a 5 s ceiling before the pulse, because a bank mid-recharge is a normal
  state and not a fault. It deliberately does not use the controller's `WaitDI`:
  that aborts the program itself on timeout, skipping the retract-and-disarm
  every other failure in that file goes through, and would leave the torch parked
  on the work at 20 lbf.

## IO — brief inventory

Only `GetDI` is used by `robot_service.py` so far (`weld_probe()`); the rest is
deliberately a brief inventory, not deep detail. Expand it when a feature
actually needs the call. Section starts ~`Robot.py:4209`. Standard shape:
`SetXX` → bare int; `GetXX` → `(0, value)`, usually bit-packed across multiple
channels.

| Call | Location | Notes |
|---|---|---|
| `SetDO(id, status, smooth=0, block=0)` | `4223` | Control-box DO, `id ∈ [0,15]`. `smooth`:0/1, `block`:0=blocking/1=non-blocking. |
| `SetToolDO(id, status, smooth=0, block=0)` | `4251` | Tool DO, `id ∈ [0,1]`. |
| `SetAO(id, value, block=0)` | `4278` | Analog out. `value` is **0–100%** — the SDK internally multiplies by `40.95` before sending (percent → raw ~0–4095 range). Don't pre-scale. |
| `SetToolAO(id, value, block=0)` | `4304` | Same scaling behavior, tool analog out. |
| `GetDI(id, block=0)` / `GetToolDI(id, block=0)` | `4330` / `4361` | **Local bit-read from `robot_state_pkg`**, not RPC (`@xmlrpc_timeout` is commented out on `GetDI`). Splits id 0–7 vs 8–15 across `cl_dgt_input_l`/`cl_dgt_input_h` bitfields. **Dead on this firmware** — the cache never fills, so every input reads 0 forever. **The raw `r.robot.GetDI(id, 0)` bypass is also dead — live-disproven 2026-07-28**: both weld interlock DIs were unreadable from the moment a run started, before any fault latched, while raw FT reads worked on the same connection. `weld_probe()` still attempts it but returns `None` for an input it cannot read rather than a confident `0` — an interlock that reads "open" when it is really unknown is the worst of the three answers. **No Python-side DI read exists on this firmware**; controller-side Lua `GetDI` inside a program is the only candidate left, itself not yet proven with a closed circuit. |
| `GetAI(id, block=0)` / `GetToolAI(...)` | `4475` / `4503` | Analog in. |
| `GetDO()` / `GetToolDO()` | `4580` / `4558` | Both **local-cache reads**, return packed hi/lo words. |
| `WaitDI`/`WaitMultiDI`/`WaitToolDI`/`WaitAI`/`WaitToolAI` | `4389–4636` | Blocking-wait-for-IO-state variants. |
| Auxiliary-axis IO (`SetAuxDO`/`SetAuxAO`/`GetAuxDI`/`GetAuxAI`/`WaitAuxDI`/`WaitAuxAI`) | `~10644–10841` | |
| Fieldbus-slave IO (`FieldBusSlaveWriteDO/AO`, `FieldBusSlaveReadDI/AI`, `FieldBusSlaveWaitDI/AI`) | `~15282–15414` | |
| IO config (`GetDIConfig`/`SetDOConfig`/`GetDIConfigLevel`/`SetDOConfigLevel`) | `~17507–17726` | |

## Force-torque sensor — how it actually couples to the robot

**The controller owns the sensor. Python is a spectator.** Nothing in this app
ever reads the sensor directly, and nothing in it is ever inside a force loop.

```
XJC sensor  ──RS485──▶  FR-16 end plate (M12, 8-core)
                            │  controller polls sensor, decouples + compensates
                            ▼
                     controller real-time state
                            │  raw XML-RPC FT_GetForceTorqueRCS(0), TCP :20003
                            ▼
              robot_service.ft_read()  ──▶  /ui/ft/reading  ──▶  UI @ 300 ms
```

**The SDK's cache path is dead on this robot** (established 2026-07-28, audit
log). The SDK intends `FtSensorRawData`/`FtSensorData`/`FtSensorActive` to
arrive via the CNDE stream (`DEFAULT_CNDE_STATES`, `Robot.py:1241-1269`, 8 ms
period) and serves them as pure local struct reads — but this FR-16 firmware
only speaks CNDE on port 20004 while the SDK targets 20005 (same root cause as
the connect-gate history in `deployment-targets`), so the CNDE connect times
out on every session and `robot_state_pkg` stays zeroed forever. Any read
through the cache reports `err=0` with all-zero forces and
`ft_sensor_active=0` no matter what the sensor does. `robot_service.ft_read()`
therefore bypasses the SDK method and calls **raw XML-RPC
`r.robot.FT_GetForceTorqueRCS(0)`** on the same proxy — the identical bypass
`robot_link._read_program_state` already uses. The raw response is flat:
`[err, fx, fy, fz, tx, ty, tz]`.

`FT_SetConfig` does not "connect" to anything — it tells the controller **which
vendor's digital protocol to speak** on the end bus. Force *control*
(`FT_Control`, `FT_Guard`) likewise closes its loop controller-side; the SDK
only hands it a setpoint and gains.

### The sensor — model and connection status

The sensor is an **XJC X-6A-XD80-H28-200N-5N.m-F(RS485)**. The trailing
`F(RS485)` is the output option: bridge excitation, amplification and decoupling
are **integrated in the sensor body**, which presents an RS485 digital interface
— the right class of device for the FR-16's end bus. No external transmitter or
DAQ is involved.

**Connected and verified live through WeldFlex itself as of 2026-07-28**: the
raw-RPC read path produces near-perfect readouts on the kiosk page, confirming
the flat `[err, fx..mz]` response shape and newton scale. (The earlier
2026-07-24 "live readout" was FAIRINO's own web UI, not WeldFlex — see the
audit log.) That settles both questions this file previously listed as open:
the cable mates to the M12 8-core end plate, and the controller's XJC driver
does talk to this model even though `Robot.py:7455` documents `device 0` as the
different `XJC-6F-D82`. Keep sending `FT_SetConfig(24, 0)`.

**Sign convention (observed live 2026-07-28)**: pressing the tool against the
work reads **negative** native Fz — tool-frame Z points out of the flange, so
compression is a −Z reaction. `app.py`'s `/ui/ft/reading` negates it for
display (`FT_FZ_DISPLAY_SIGN = -1.0`); `ft_read()` returns the native sign.

> An earlier revision of this file claimed this sensor could not reach the
> controller at all without an RS485 transmitter module. That was wrong — it was
> read off the X-6A family datasheet's **analog** variant (5–10 VDC excitation,
> 0.2–1.0 mV/V, 14-pin breaking out six bridge pairs). That spec table and its
> pinout describe a different unit; don't wire from them. Audit log, 2026-07-24.

Still unconfirmed: whether the app's **Initialize** path (`ft_setup()` →
`FT_SetConfig(24,0)` → `FT_SetRCS(0)` → activate → zero) has been exercised
end-to-end on hardware, or whether the active config came from FAIRINO's
pendant/web UI. If the latter, the reporting frame is not necessarily tool
frame — run Initialize from the kiosk page and re-check the hand-push test.

### Commissioning checklist

A live readout is **not** proof of a correct setup: the read path reports success
unconditionally (see the `FT_GetForceTorqueRCS` gotcha below), so several
distinct faults all present as plausible-looking numbers. Check in order:

1. ~~`ft_sensor_active == 1`~~ — **unavailable on this firmware.** The flag only
   ever arrives via the CNDE stream, which never connects (see above), so it
   reads 0 forever and says nothing about the sensor. `reading["active"]` is now
   hardcoded True on any successful raw-RPC read; the UI banner means "the
   controller answered a force query", not "the sensor is alive". Liveness must
   come from checks 2–5. (Whether the raw RPC returns a nonzero error for an
   unplugged/unconfigured sensor is still unverified — the 2026-07-28 session
   ended in a controller crash before this could be tested.)
2. **Values dither.** Sample for a few seconds with nothing touching the tool.
   Real strain-gauge data always moves in the low digits; a bit-for-bit constant
   is a frozen feed. The observed span is also the noise floor — a force
   threshold below roughly 3× the Fz span will chatter.
3. **`FT_GetForceTorqueRCS` ≠ `FT_GetForceTorqueOrigin`.** Identical values mean
   decoupling/zeroing is not being applied.
4. **Unloaded reads ≈ 0** after Re-Zero with the tool hanging free. A large
   standing offset means the zero didn't take, or tool-weight compensation is
   unset.
5. **Push the torch tip by hand.** The expected axis must move, with the expected
   sign, and settle back on release. This is the only check that validates axis
   mapping and confirms `FT_SetRCS(0)` (tool frame) actually took effect.
   **Known sign convention (observed live 2026-07-28): pressing the tool against
   the work reads *negative* native Fz** — tool-frame Z points out of the
   flange, so compression is a −Z reaction. `app.py`'s `/ui/ft/reading` negates
   it for display (`FT_FZ_DISPLAY_SIGN`); `robot_service.ft_read()` returns the
   native sign, so control logic consuming it must expect negative-on-press.
6. **Magnitude sanity.** 200 N is full scale on this unit. Readings an order of
   magnitude off indicate a units/scaling mismatch in the vendor protocol.

Don't go looking for an analog workaround if something here fails: the robot
exposes three analog inputs total (`cl_analog_input[2]`, `tl_anglog_input`,
`Robot.py:224-225`), they can't represent six axes, and they're generic IO reads
(`GetAI`, `:4475`) that never feed the FT pipeline.

## Force-torque sensor — currently used, validated

- **`FT_SetConfig(self, company, device, softversion=0, bus=0)`** —
  `Robot.py:7463`. `company`: `17`=Kunwei, `19`=Aerospace-11th-Academy,
  `20`=ATI, `21`=Zhongke MiDian, `22`=Weihang Minxin, `23`=NBIT,
  **`24`=XJC (鑫精诚) — the sensor in use on WeldFlex**, `26`=NSR. `device`:
  vendor-specific model index (`0` in all examples). `softversion`/`bus`:
  unused, default `0`.
- **`FT_Activate(self, state)`** — `Robot.py:7488`. `state`: `0`=reset,
  `1`=activate.
- **`FT_SetZero(self, state)`** — `Robot.py:7510`. `state`: `0`=remove zero
  offset, `1`=apply zero correction.
- **`FT_SetRCS(self, ref, coord=[0,0,0,0,0,0])`** — `Robot.py:7533`. Selects the
  frame `FT_GetForceTorqueRCS` reports in: `ref` `0`=tool, `1`=base; `coord`
  optionally supplies a custom frame. `ft_setup()` sets `FT_SetRCS(0)` (tool
  frame) so the reported frame is asserted rather than inherited from whatever
  the controller was last left in. **Tool frame is deliberate for WeldFlex**:
  weld contact force acts along the torch approach axis, which is fixed in the
  tool frame but smears across base-frame axes as the robot reorients — and a
  single tool-frame component is what `FT_Control`'s `select` mask will want.
- **`FT_GetForceTorqueRCS(self)`** — `Robot.py:7655`. **Local-cache read, not
  RPC** (real call commented out at line 7659; body just returns
  `0, [robot_state_pkg.ft_sensor_data[0..5]]`). **Dead on this firmware** — the
  cache never fills (see above), so it always returns zeros with err 0. Don't
  call the SDK method; `ft_read()` uses the raw XML-RPC
  `r.robot.FT_GetForceTorqueRCS(0)` instead (flat
  `[err, fx, fy, fz, tx, ty, tz]` response, per the commented-out code).
  **The raw read returns error 14 for the whole time a force-control move
  (`FT_FindSurface`) is executing** (live 2026-07-28) — the controller's
  force-control task owns the sensor. That is routine, not a fault:
  `weld_probe()` reports it as `ft_err` in its dict instead of raising, and
  the weld-test page renders running+14 as "sensor busy"
  (`FT_RPC_BUSY_CODE` in `app.py`). The same code also appears when a latched
  controller fault blocks all raw reads — see
  `error-handling-and-connection.md` for telling the two apart.
- **`FT_GetForceTorqueOrigin(self)`** — `Robot.py:7679`. Same dead local-cache
  pattern; the raw RPC is `r.robot.FT_GetForceTorqueOrigin(0)`. Useful as a
  cross-check: if RCS and Origin are identical, decoupling/zeroing is not being
  applied.
- **`FT_GetConfig(self)`** — `Robot.py:7435`. **Not a trustworthy readback.** Its
  docstring promises `[number, company, device, softversion, bus]` (5 values) but
  the body returns 4, with `+1` added to the first two (`:7448`). Don't compare
  its output against what you passed to `FT_SetConfig` without accounting for
  that.

### Validated init sequence

Confirmed identical in the official PDF (§2.4.12.13) and
`windows/example/TestForceControlCommand.py:23-40`:

```
FT_SetConfig(company, device)  → sleep(1)
FT_Activate(0)   # reset       → sleep(1-2)
FT_Activate(1)   # activate    → sleep(1-2)
FT_SetZero(0)    # clear zero  → sleep(1-2)
FT_SetZero(1)    # apply zero  → sleep(1-2)
```

`backend/robot_service.py`'s `ft_setup()` matches this exactly
(`FT_SetConfig(24, 0)` for the XJC sensor, `FT_Activate(0)`→sleep(2)→
`FT_Activate(1)`→sleep(2)→`FT_SetZero(0)`→sleep(2)→`FT_SetZero(1)`, ~10s
total) — slightly more conservative sleeps than the doc's uniform 1s. **This
is a validated, authoritative reference for the required settle times** — use
it as-is rather than re-deriving timing.

## FT_FindSurface — live-validated controller-side (2026-07-28)

Used by `programs/weld.lua` (SEARCH and PRESS phases) as the **controller-side
Lua instruction**, not the SDK method — WeldFlex never calls it from Python.
Proven param order: `FT_FindSurface(rcs, dir, axis, lin_v, lin_a, disMax, ft)`
with `rcs` 0=tool/1=base, `dir` 0=negative/1=positive, `axis` 1=X/2=Y/3=Z
(1-indexed), speeds in mm/s, `disMax` in mm, `ft` in N. Returns 0 on contact.
Three live findings:

- **Reaching `disMax` without contact hard-aborts, and that abort latched an
  axis-2 joint-overspeed fault** ("command speed in joint space of axis 2
  exceeds the limit"), deterministically, run after run. Contact within
  `disMax` stops cleanly. So a mis-sized `disMax` presents as a *motion* fault,
  not as the "no surface found" error you'd expect — size `disMax` so a real
  surface is reachable (remember it measures from the *start* of the move; a
  10 mm Z-clearance park eats 10 mm of it). `JointOverSpeedProtectStart`
  (armed per-run via `robot_service.joint_overspeed_protect`) did **not**
  prevent the latch — it governs commanded motion, not an abort's decel spike.
- **Host-side raw FT reads return code 14 for the whole time it executes**
  (see `FT_GetForceTorqueRCS` above). `GetCurrentLine` keeps answering.
- The `ft` threshold (10 N contact) triggers reliably; the fault path in
  weld.lua (retract + Lua `error()`) was exercised live. A Lua `error()` does
  **not** latch a clearable controller fault — its message reaches the pendant
  console only.

## Force-torque sensor — not yet used

Carried forward from a prior investigation note (`force_sensor.md`, now
superseded — see the audit log). These functions aren't called anywhere in
`robot_service.py` yet, but were confirmed to exist and are the mechanism to
reach for if WeldFlex needs active force control during weld travel (e.g.
holding ~30 lbf / ~133 N of contact force while the torch moves):

| Call | Location | Purpose |
|---|---|---|
| `FT_Control(...)` | `7751` | Constant-force control on a selected active axis; force/torque target units documented as N/Nm at `7734`. Primary mechanism for holding a target contact force during linear weld travel. **SDK bug at `7784`**: the packed parameter list is built as `[M[0], M[1], B[0], B[0], ...]` — `B[0]` twice, silently dropping `B[1]`, so the second damping parameter can never reach the controller. Assume damping is single-valued until this is patched. |
| `FT_Guard(...)` | `7694` | Collision/force guarding — configurable threshold envelope around a setpoint (`force_torque ± threshold`, range definition at `7702`). Use as a safety check alongside `FT_Control`. |
| `FT_FindSurface(...)` | `7950` | SDK-side wrapper of the surface-seek above — unused; weld.lua calls the controller-native Lua form directly. |
| `FT_ComplianceStart(...)` | `7999` | Compliance mode with a force threshold (N) — controlled give when path disturbance handling is needed. |
| `ImpedanceControlStartStop(...)` | `15949` (docs) / `15962` (impl) | Cartesian/joint impedance control with a force threshold (N) — alternative to `FT_ComplianceStart` for compliant motion. |

Example flow showing FT control enabled before linear motion and disabled
after: `windows/example/TestForceControlCommand.py:93,96`.

**Practical interpretation** (unvalidated against a live robot as of this
writing — see the audit log's "next checks"): `FT_Control` with the contact
axis selected and a target force near 133 N is the primary mechanism; `FT_Guard`
provides the safety envelope; `FT_FindSurface` gives consistent contact
acquisition; `FT_ComplianceStart`/`ImpedanceControlStartStop` are for cases
needing controlled compliance to path disturbance. Before wiring this in:
dry-run at low force and ramp up while monitoring path error and arc
stability, and confirm axis sign/coordinate-frame mapping for the active force
axis — no live commissioning test has been run for this yet.

**Clear the commissioning checklist first.** Don't wire any of this until the
sensor passes the checks above (item 1 is void on this firmware — liveness
comes from checks 2–5) — in particular a hand push moving the expected axis,
remembering native Fz reads **negative** on press. `FT_Control` against a
sensor stuck at all-zeros is worse than no force control at all: the loop reads
"no contact" indefinitely and keeps driving in.
