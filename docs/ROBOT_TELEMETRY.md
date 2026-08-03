# Robot Telemetry Standard

This document defines the production communication path between WeldFlex and
the FAIRINO controller. It applies to the Windows development machine and the
Raspberry Pi kiosk.

## Transport and Ownership

- Commands and core state use the controller's XML-RPC endpoint on TCP `20003`.
  Continuous real-time telemetry uses the CNDE stream on TCP `20005`, configured
  by `WELDFLEX_CNDE_PORT`. WeldFlex configures real-time subscriptions (`FtSensorData`,
  `ProgramState`, `RobotState`, `MainCode`, `SubCode`, `RobotMode`, `EmergencyStop`, `MotionDone`)
  before the client is constructed so telemetry reads perform 0-latency non-blocking reads from `robot_state_pkg`.
- `backend/robot_link.py` owns the connection lifecycle. It builds and retires
  clients on the supervisor thread, but the `robot-sdk-*` worker is the only
  thread allowed to invoke either SDK methods or `client.robot` raw XML-RPC
  methods.
- No Flask route or browser poll may call the controller. Routes render cached
  snapshots. `RobotLink` publishes each complete CNDE frame to its immutable
  `ForceSnapshot`; `WeldFlexRobotService.force_snapshot()` is the shared,
  read-only force API for all pages. The weld-test sampler dispatches its other
  details through the same SDK worker.
- Worker order is: operator command, core heartbeat, detailed telemetry. Equal
  detailed requests with the same connection generation share one worker future
  rather than queueing duplicate controller reads.

## Data Sources

| Signal | Source | Interpretation |
|---|---|---|
| Connection liveness | `GetControllerIP()` raw XML-RPC | A failed transport is a link failure. |
| Program state | `GetProgramState()` raw XML-RPC | SDK wrapper is a dead CNDE-cache read on this firmware. |
| Current line | `GetCurrentLine()` raw XML-RPC | Drives the job manager's cached cycle tracker. |
| Controller fault | `GetRobotErrorCode()` raw XML-RPC | Cache fallback is trusted only when CNDE is demonstrably streaming. |
| Force/torque | CNDE `FtSensorData` → `ForceSnapshot` | Fresh CNDE frames are the only force source during controller-side force control. Raw `FT_GetForceTorqueRCS(0)` is an idle-only fallback; it returns code `14` while the force task owns the sensor. |
| Lua phase/return values | `GetSysVarValue()` XML-RPC | Controller Lua publishes phase, force-return, press data, and DI levels to slots 1–7. |
| DI0/DI1 during weld test | Controller-side `GetDI()` → system variables 6/7 → `GetSysVarValue()` | `-1`/unset is `unknown`. The host does not poll raw `GetDI()` mid-run because that call can stall the shared XML-RPC worker on this firmware. Do not use as a host-side interlock. |

## Freshness and Generations

- Core state, line, and fault data are held in `ConnSnapshot`. A running job
  enables the fast core heartbeat at 250 ms.
- Weld-test details are held in `WeldTelemetrySnapshot`. The sampler runs at
  400 ms during a weld test and 1200 ms while its page is idle.
- Force is held in `ForceSnapshot`, updated after every complete CNDE frame.
  The initial subscription contains only `FtSensorData` at 20 ms, configured by
  `WELDFLEX_CNDE_PERIOD_MS`. It is fresh for 500 ms by default; stale data is
  unavailable, never retained as a plausible force value.
- A detailed sample remains fresh while it is no older than its active freshness
  budget and belongs to the current connection generation. A failed replacement
  sample keeps the prior complete reading visible until it ages out; unreadable
  values remain unavailable, never zeroes.
- Reconnects and retargets advance the client generation. A result from a
  retired generation is discarded before it can refresh either cache.
- A raw method is recorded as unsupported only when the controller returns an
  XML-RPC method-not-found fault. A malformed or transient reply is one failed
  sample and is retried on the next heartbeat. Capability detection resets for
  every new client.

## Recovery Procedure

1. Read the connection and telemetry age on Robot Diagnostics or Weld Test.
2. If data is stale or the link is faulted, use the in-app Reconnect control.
3. If controller faults persist, clear them through the approved operator UI or
   the pendant as required by the controller; code `14` on every raw read after
   a program has stopped is not fixed by reconnecting alone.
4. Verify a new connection generation and fresh CNDE force frames before
  resuming observation. Compare an idle CNDE sample with the raw XML-RPC
  fallback before relying on it during force control. Telemetry is observe-only;
  it does not establish a safety interlock or authorize motion.

Do not run ad-hoc XML-RPC or CNDE scripts against the live controller. CNDE
validation is app-based and read-only: prove fresh, changing frames while idle,
compare them to the XML-RPC fallback, then verify the stream stays fresh through
one controller-side force operation.