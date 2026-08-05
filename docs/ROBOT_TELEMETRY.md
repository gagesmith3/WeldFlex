# Robot Telemetry Standard

This document defines the production communication path between WeldFlex and
the FAIRINO controller. It applies to the Windows development machine and the
Raspberry Pi kiosk.

It is current-tense and describes what the code does **today**. The migration
onto port 8083 is partway through; the "Still on the old path" section below
says exactly what has not moved yet. Trust this file over the skills or any
inline comment that disagrees.

## Transports

Three separate channels reach the controller. They fail independently, and that
independence is now load-bearing rather than incidental.

| Channel | Port | Direction | Carries |
|---|---|---|---|
| XML-RPC | `20003` | request/response | **All commands**, plus a liveness heartbeat |
| Status feed | `8083` | controller push | Program state, line, fault codes, DI/DO, F/T, TCP pose, e-stop, robot mode |
| CNDE | `WELDFLEX_CNDE_PORT` | controller push | Force only (legacy — see below) |

### Why the split matters

XML-RPC is a poll, and the controller **stops answering it while it is busy
with a force operation**. Observed live 2026-08-03: an `FT_FindSurface` reliably
kills the XML-RPC link for the duration of the search while the program keeps
running normally to completion. The port-8083 socket rode straight through the
same window — `connects=1`, `generation=1`, zero checksum failures.

So a dead XML-RPC link no longer means "the robot is gone". WeldFlex reports the
two channels separately and never lets one imply the other:

- `UniversalRobotState.commands_available` — XML-RPC is up. **Anything gating a
  command must read this.**
- `UniversalRobotState.feed_streaming` — a frame arrived within
  `WELDFLEX_FEED_STALE_S`. Observation only. Never a command gate.
- `UniversalRobotState.telemetry_source` — `"8083"` or `"rpc"`, whichever
  supplied the state in this snapshot.

When the feed is live but XML-RPC is not, the connection state becomes
`telemetry` (amber chip, `TELEMETRY`, detail "no commands"). This is deliberately
neither `ONLINE` nor `OFFLINE`: the arm is plainly moving, so red is a lie the
operator can see through — but green would imply Stop works, and it would
silently do nothing. An operator `disconnect` is an intent, not a failure, and a
live feed never overrides it.

## Ownership

- `backend/robot_feed.py` owns the 8083 socket: its own daemon thread, its own
  backoff, and its own generation counter, deliberately **not** tied to the
  XML-RPC client's. An RPC reconnect must not blank telemetry, and a feed frame
  must not be discarded because the RPC client cycled.
- `backend/frame_8083.py` is the pure decoder — no I/O, fully testable offline.
  It parses via a named offset table with a per-field length guard rather than
  one `struct.unpack`, so a firmware revision that appends or trims fields
  degrades field-by-field instead of throwing the whole frame away.
- `backend/robot_link.py` owns the XML-RPC connection lifecycle. It builds and
  retires clients on the supervisor thread, but the `robot-sdk-*` worker is the
  only thread allowed to invoke SDK or raw `client.robot` methods.
- No Flask route or browser poll may call the controller. Routes render cached
  snapshots only. All three caches — `ConnSnapshot`, `ForceSnapshot`,
  `FeedSnapshot` — are immutable and reference-swapped: a reader takes the
  reference under a lock and is then free of it.
- Worker priority is: operator command, core heartbeat, detailed telemetry.
  Equal detailed requests sharing a connection generation share one worker
  future rather than queueing duplicate controller reads.

## Data Sources

| Signal | Source | Interpretation |
|---|---|---|
| Program state | 8083 `program_state` (offset 0) | `1` stop, `2` run, `3` paused, `4` drag. `f8.PROGRAM_STATES` and `robot_service.STATE_MAP` agree exactly, so the cutover changed source, not meaning. Falls back to `GetProgramState()` raw XML-RPC when no fresh frame. |
| Current line | 8083 `prog_cur_line` (offset 172) | Display only. **The cycle tracker does not read this** — see below. |
| Controller fault | 8083 `main_errcode` / `sub_errcode` (412/416) | The frame reports `0` for "no fault"; `ConnSnapshot` uses `None`. Do not conflate — `get_universal_state()` normalises `0` to `None`. |
| Connection liveness | `GetControllerIP()` raw XML-RPC | A failed transport is an XML-RPC failure, not necessarily a robot failure. Check `feed_streaming` before calling it offline. |
| Force/torque | CNDE `FtSensorData` → `ForceSnapshot` | **Still on CNDE.** Raw `FT_GetForceTorqueRCS(0)` is an idle-only fallback; it returns code `14` for the whole time a force move owns the sensor. 8083 carries `FT_data[0..5]` at offset 179 and is decoded already, but nothing reads it yet. |
| Lua phase/return values | `GetSysVarValue()` XML-RPC | 8083 carries no system variables, so slots 1–5/8 stay on XML-RPC permanently. They are the only window into a running Lua program. |
| DI0/DI1 during weld test | Controller-side `GetDI()` → sysvars 6/7 → `GetSysVarValue()` | **Still on the Lua relay.** 8083 carries the DI bitmap at 176/177 and `FeedSnapshot.di(n)` decodes it, but no consumer has been switched over. |

**Telemetry is observe-only.** It does not establish a safety interlock and does
not authorize motion. This applies to feed DI specifically: `FeedSnapshot.di()`
is a display value and must not be used as an interlock.

## Freshness and Generations

- A frame is usable for `WELDFLEX_FEED_STALE_S` (default `3.0` s) — roughly 30
  missed frames at the controller's slowest send period. Long enough to ride out
  a hiccup, short enough that nobody reads second-old data believing it is live.
  `is_fresh()` also requires the frame to be non-empty, so "connected but never
  decoded a frame" never reads as fresh.
- The 8083 send period is set **on the pendant** (system settings → maintenance
  mode), range 8–100 ms. There is no host-side setting for it.
- Force is held in `ForceSnapshot`, fresh for `WELDFLEX_CNDE_FORCE_FRESH_S`
  (default `0.5` s). Stale force is unavailable, never retained as a plausible
  value.
- Weld-test details are held in `WeldTelemetrySnapshot`, sampled at 400 ms
  during a weld test and 1200 ms while its page is idle. A failed replacement
  sample keeps the prior complete reading visible until it ages out; unreadable
  values stay unavailable, never zeroes.
- Reconnects and retargets advance the XML-RPC client generation. A result from
  a retired generation is discarded before it can refresh either cache. The feed
  has its own independent generation, incremented only on a successful TCP
  connect.
- A raw method is recorded as unsupported only on an XML-RPC method-not-found
  fault. A malformed or transient reply is one failed sample, retried next
  heartbeat. Capability detection resets for every new client.

### Endianness

The vendor doc does not state byte order. `robot_feed.py` auto-detects it on the
first frames and latches the result, and `looks_sane()` refuses to publish a
frame that decodes implausibly rather than showing garbage. Confirmed
**little-endian** with `DATA` length 650 on this firmware, 2026-08-03.

## Still on the old path

Do not write code or docs that assume these have moved.

1. **Force still comes from CNDE**, not the feed. The CNDE port is also
   suspect: `robot_link.py` defaults to `20005`, the live `.env` sets no CNDE
   key at all, and `20005` is the port that has never worked here. Assume force
   may be dead in production until proven otherwise on hardware.
2. **DI still comes from the controller-Lua → sysvar relay**, not the feed's DI
   bitmap.
3. **Cycle tracking still rides XML-RPC.** `job_manager.py` reads
   `self._robot.snapshot()` — the link's `ConnSnapshot`, filled by the XML-RPC
   heartbeat — not `get_universal_state()`. It therefore still stalls during the
   find-surface window, and it still inherits `GetCurrentLine`'s sub-file line
   semantics. The planned replacement is a DO4–DO7 rolling counter read from the
   frame's DO bitmap, which is blocked on confirming those outputs are unwired.
4. **The XML-RPC heartbeat has not been shrunk.** It still costs three round
   trips and still doubles its rate during a run.

## Recovery Procedure

1. Read the connection state and telemetry age on Robot Diagnostics or Weld
   Test. The Status Feed panel shows frame counters, checksum failures, resyncs
   and `LEN`, side by side with the XML-RPC values.
2. `TELEMETRY` (amber) means the robot is fine and commands are not getting
   through — normal during a force operation, and it should clear on its own.
   It is not a reason to reconnect mid-run.
3. If data is genuinely stale or the link is faulted, use the in-app Reconnect
   control.
4. If controller faults persist, clear them through the approved operator UI or
   the pendant. Code `14` on every raw read after a program has stopped is a
   latched fault and is not fixed by reconnecting alone.
5. Verify a new connection generation and fresh frames before resuming
   observation.

Do not run ad-hoc XML-RPC, CNDE or 8083 scripts against the live controller — a
diagnostic script crashed it on 2026-07-28. Validation is app-based and
read-only. `tools/feed_continuity.py` is safe by construction: it polls the local
Flask panel and performs no robot I/O at all, which makes it the right instrument
for continuity questions the cumulative counters cannot answer (a stream that
goes quiet with the socket still open leaves `connects` and `generation` looking
perfect).
