# IO & force-torque sensor

## IO — brief inventory

Not used by `robot_service.py` yet — this is deliberately a brief inventory,
not deep detail. Expand this section when a feature actually needs IO.
Section starts ~`Robot.py:4209`. Standard shape: `SetXX` → bare int; `GetXX` →
`(0, value)`, usually bit-packed across multiple channels.

| Call | Location | Notes |
|---|---|---|
| `SetDO(id, status, smooth=0, block=0)` | `4223` | Control-box DO, `id ∈ [0,15]`. `smooth`:0/1, `block`:0=blocking/1=non-blocking. |
| `SetToolDO(id, status, smooth=0, block=0)` | `4251` | Tool DO, `id ∈ [0,1]`. |
| `SetAO(id, value, block=0)` | `4278` | Analog out. `value` is **0–100%** — the SDK internally multiplies by `40.95` before sending (percent → raw ~0–4095 range). Don't pre-scale. |
| `SetToolAO(id, value, block=0)` | `4304` | Same scaling behavior, tool analog out. |
| `GetDI(id, block=0)` / `GetToolDI(id, block=0)` | `4330` / `4361` | **Local bit-read from `robot_state_pkg`**, not RPC (`@xmlrpc_timeout` is commented out on `GetDI`). Splits id 0–7 vs 8–15 across `cl_dgt_input_l`/`cl_dgt_input_h` bitfields. |
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
                            │  CNDE stream, TCP :20005, 8 ms period
                            ▼
              SDK RobotStatePkg.ft_sensor_data[6] / ft_sensor_active
                            │  FT_GetForceTorqueRCS() = local struct read, no RPC
                            ▼
              robot_service.ft_read()  ──▶  /ui/ft/reading  ──▶  UI @ 300 ms
```

`FT_SetConfig` does not "connect" to anything — it tells the controller **which
vendor's digital protocol to speak** on the end bus. `FtSensorRawData`/
`FtSensorData`/`FtSensorActive` are members of `DEFAULT_CNDE_STATES`
(`Robot.py:1241-1269`), streamed at `DEFAULT_CNDE_PERIOD = 8` ms (`:1318`), which
is why the getters can be pure local reads. Force *control* (`FT_Control`,
`FT_Guard`) likewise closes its loop controller-side; the SDK only hands it a
setpoint and gains.

### The sensor — model and connection status

The sensor is an **XJC X-6A-XD80-H28-200N-5N.m-F(RS485)**. The trailing
`F(RS485)` is the output option: bridge excitation, amplification and decoupling
are **integrated in the sensor body**, which presents an RS485 digital interface
— the right class of device for the FR-16's end bus. No external transmitter or
DAQ is involved.

**Connected to the robot and producing a live readout as of 2026-07-24.** That
settles both questions this file previously listed as open: the cable mates to
the M12 8-core end plate, and the controller's XJC driver does talk to this
model even though `Robot.py:7455` documents `device 0` as the different
`XJC-6F-D82`. Keep sending `FT_SetConfig(24, 0)`.

> An earlier revision of this file claimed this sensor could not reach the
> controller at all without an RS485 transmitter module. That was wrong — it was
> read off the X-6A family datasheet's **analog** variant (5–10 VDC excitation,
> 0.2–1.0 mV/V, 14-pin breaking out six bridge pairs). That spec table and its
> pinout describe a different unit; don't wire from them. Audit log, 2026-07-24.

Still unconfirmed: whether that readout came through this app's **Initialize**
(`ft_setup()` → `FT_SetConfig(24,0)` → `FT_SetRCS(0)` → activate → zero) or
through FAIRINO's own pendant/web UI. If the latter, the app's config path is
still unexercised and the reporting frame is not necessarily tool frame — run
Initialize and re-check.

### Commissioning checklist

A live readout is **not** proof of a correct setup: the read path reports success
unconditionally (see the `FT_GetForceTorqueRCS` gotcha below), so several
distinct faults all present as plausible-looking numbers. Check in order:

1. **`ft_sensor_active == 1`** — surfaced as `reading["active"]`. Zero here
   alongside zero forces means "no sensor", never "no load". It also reads `0`
   when the *robot* is unreachable, so confirm the connection first or this
   check is meaningless.
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
  `0, [robot_state_pkg.ft_sensor_data[0..5]]`). **It cannot fail** — an
  unconfigured, unplugged, or physically absent sensor is indistinguishable from
  a genuinely zero load. `ft_sensor_active` (surfaced by `ft_read()` as
  `reading["active"]`) is the only liveness signal; treat a zeros reading with
  `active == 0` as "no sensor", never as "no load".
- **`FT_GetForceTorqueOrigin(self)`** — `Robot.py:7679`. Same local-cache
  pattern, raw (unfiltered) F/T data. Useful as a cross-check: if RCS and Origin
  are identical, decoupling/zeroing is not being applied.
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
| `FT_FindSurface(...)` | `7950` | Surface-seek motion that terminates on a force threshold (N) — for consistent contact acquisition before starting force control. |
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
sensor passes all six checks above — in particular `ft_sensor_active == 1` and a
hand push moving the expected axis. `FT_Control` against a sensor stuck at
all-zeros is worse than no force control at all: the loop reads "no contact"
indefinitely and keeps driving in.
