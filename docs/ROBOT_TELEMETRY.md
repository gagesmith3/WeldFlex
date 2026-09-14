# Robot Telemetry Standard

This document defines the production communication path between WeldFlex and
the FAIRINO controller. It applies to the Windows development machine and the
Raspberry Pi kiosk.

It is current-tense and describes what the code does **today**. The migration
onto port 8083 is essentially complete; the "Migration ledger" section below
says what moved, what did not, and what never will. Trust this file over the
skills or any inline comment that disagrees.

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
| Current line | 8083 `prog_cur_line` (offset 172) | Drives the **cycle tracker** as well as the display, since `f41dd0a`. Falls back to the XML-RPC `current_line` when no fresh frame. The `program_max_line` ceiling still applies either way — a `NewDofile`'d sub-program reports its own line numbers, and the source change did not alter that. |
| Controller fault | 8083 `main_errcode` / `sub_errcode` (412/416) | The frame reports `0` for "no fault"; `ConnSnapshot` uses `None`. Do not conflate — `get_universal_state()` normalises `0` to `None`. |
| Connection liveness | `GetCurrentLine()` raw XML-RPC | The heartbeat's single mandatory round trip — see "The heartbeat proves XML-RPC" below. A failed transport is an XML-RPC failure, not necessarily a robot failure. Check `feed_streaming` before calling it offline. |
| Force/torque | 8083 `FT_data[0..5]` (offset 179) | Primary source for `ft_read()` and the F/T page. The push survives controller-side force operations. CNDE `FtSensorData` is the compatibility fallback. **`ft_read()` reads no further than those two caches** — its old raw `FT_GetForceTorqueRCS(0)` fallback was removed, so a stale cache is now reported as no reading rather than answered with an RPC that returns code `14` for the whole of a force move. |
| Lua phase/return values | `GetSysVarValue()` XML-RPC | 8083 carries no system variables, so slots 1–5/8 stay on XML-RPC permanently. The Job Manager starts the detailed sampler while a weld program runs; these values are the only window into the controller-applied press target and other Lua state. `weld_probe` issues **one bounded call per slot** rather than one batched dispatch — see "Sampling is interruptible" below. |
| DI0/DI1 display | 8083 DI bitmap (176/177) via `FeedSnapshot.di(n)` | **Feed-first as of this revision.** `get_universal_state()` overrides the sysvar levels with `feed.di()` whenever the frame is fresher than `FORCE_FRESH_S`, and `weld_probe` answers sysvar slots 6/7 from the frame without issuing their RPC reads at all. The controller-side `GetDI()` → sysvars 6/7 relay remains underneath as the fallback when no fresh frame is available. |

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
- **Force reads apply a tighter budget than the general feed one.** `ft_read()`
  passes `robot_service.FORCE_FRESH_S` (`0.5` s) to *both* caches, so a frame
  good enough to report program state at `WELDFLEX_FEED_STALE_S` (3 s) is not
  automatically good enough to report a force. The same 0.5 s budget gates the
  feed's DI and TCP-pose values in `get_universal_state()`, for the same reason:
  a number an operator reads as "now" gets a now-sized window.
- Detailed weld values are held in `WeldTelemetrySnapshot`, sampled at
  `JOB_TELEMETRY_INTERVAL_S` (250 ms) during a Job Manager run. A failed
  replacement sample keeps the prior complete reading visible until it ages out;
  unreadable values stay unavailable, never zeroes.
- **A stale or foreign weld sample yields nothing, field by field.**
  `WeldTelemetrySnapshot.sysvar()` returns `None` once the snapshot is past
  `WELD_TELEMETRY_FRESH_S`, so an expired sample cannot leak a value through an
  accessor that skipped the freshness check. `get_universal_state()` adds the
  stricter test — the sample must also carry the *current* XML-RPC generation —
  before it reads any slot, so values captured against a since-retired
  connection are dropped rather than mixed into a live snapshot.
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

## Host-side discipline

Four rules govern what the host is allowed to do to the controller. They are
enforced in code, and each has a test pinning it.

### Reads come from cache; only the heartbeat and commands touch the wire

`get_universal_state()` and `ft_read()` are **pure cache reads** — they consult
`FeedSnapshot`, `ForceSnapshot` and `WeldTelemetrySnapshot` and issue no RPC of
their own, however stale those caches are. A route that renders "no reading" has
not fallen back to the robot to try harder; falling back is what was removed.
The consequence worth internalising: **a page that shows stale telemetry costs
the controller nothing**, so an operator staring at a frozen readout is not the
reason commands are slow.

### The heartbeat proves XML-RPC

`_probe_body` is the only thing that sets `CONNECTED`, and `CONNECTED` is the
only thing that gates `commands_available`. Because its state and fault reads
are now answered from pushed data, `GetCurrentLine()` is the one call left that
touches the network — so it is mandatory, it runs under a
`TELEMETRY_RPC_TIMEOUT_S` (0.5 s) transport limit, and a transport failure from
it is re-raised rather than swallowed. Without that, a live feed would let the
probe report `CONNECTED` over a completely dead command channel and Stop would
silently do nothing.

The same reasoning applies to the CNDE cache the state readers prefer: the SDK's
`_robot_state_run_flag` latches `True` over a dead stream, so `_cnde_streaming()`
additionally requires the receive thread to still be alive. A latched flag over
a frozen `robot_state_pkg` is indistinguishable from live data otherwise.

### Sampling is interruptible between reads

The detailed sampler no longer batches its reads into one long worker dispatch.
`weld_probe` walks its slots one at a time against a
`WELD_TELEMETRY_CALL_TIMEOUT_S` budget, each read capped at
`TELEMETRY_RPC_TIMEOUT_S`, and **aborts the whole sample on the first failure**
rather than pressing on. `RobotLink.call()` supports this with a `rpc_timeout`
argument that narrows the transport's socket timeout for one call only, a
deadline check that drops a queued call whose caller has already given up, and a
`future.cancel()` when the wait expires.

The point is worker availability, not sample latency. The link has a single SDK
worker; an operator pressing Stop waits behind whatever telemetry read is in
flight. Short, individually bounded reads keep that wait to one read rather than
one batch, and a caller that gave up cannot leave work queued in front of a
command. A failed sample is not a gap in the display — the previous complete
reading stays visible until it ages out.

### The force display streams, and falls back to polling

`/ui/ft/reading` still renders the reading partial on request, but nothing polls
it with HTMX any more. `/ui/ft/stream` pushes that same partial over
Server-Sent Events at **10 Hz** (`time.sleep(0.1)`), capped
at **four concurrent streams** by a `BoundedSemaphore` — a fifth viewer gets
`429` with `Retry-After` rather than a slot. Each stream self-terminates after
30 s and advertises `retry: 2000`, so a browser reconnects on its own and no
stream is held open indefinitely.

`backend/static/js/ft_live.js` drives it and degrades in three ways: no
`EventSource` support, or a stream error, drops it to `fetch('/ui/ft/reading')`
polling; a gap of more than a second without a message reconnects; and hiding
the tab closes the stream entirely. None of this adds robot traffic — the
stream is reading the same cache the poll was, just more often. That is the only
reason 10 Hz is acceptable at all, and it stops being true the moment anything
behind `/ui/ft/reading` issues an RPC.

## Migration ledger

The 8083 cutover happened one signal at a time, and each step falsified a
sentence somewhere. This is the current state — read it before asserting that
anything is or is not still on XML-RPC.

**Lua system variables stay on XML-RPC permanently.** 8083 carries no sysvars,
so the phase code, the last `FT_*` return and the press diagnostics (slots 1–5
and 8) have nowhere else to come from. Only the two DI slots (6/7) have a feed
equivalent, and they now use it.

**DI display has moved.** `get_universal_state()` prefers `FeedSnapshot.di()`
over the sysvar levels, and `weld_probe` answers slots 6/7 from the frame
instead of spending an RPC read on each. The Lua relay is the fallback, not the
source. It remains **observe-only** either way — see the note above.

**The XML-RPC heartbeat has been shrunk.** `_probe_body` now makes exactly one
round trip (`GetCurrentLine`) whenever a fresh frame can supply program state
and fault codes, instead of three; and `_tick_interval` no longer doubles the
beat rate during a run while the feed is fresh, because the thing that made a
run worth polling harder is now arriving on its own. Both fall back to the old
behaviour the moment the frame goes stale.

**Cycle tracking has moved** (`f41dd0a`). `_monitor_body` reads
`self._robot.get_universal_state()`, so the tracker observes the 8083 line and
rides through the find-surface window like everything else. The DO4–DO7 rolling
counter is no longer needed for outage survival; it would only remove the
remaining dependence on line numbers. Earlier revisions of this file listed
cycle tracking here — that entry is retired, not merely reworded.

## Recovery Procedure

1. Read the connection state and telemetry age on Robot Diagnostics. The Status
   Feed panel shows frame counters, checksum failures, resyncs and `LEN`, side
   by side with the XML-RPC values. (The Weld Test page this step used to name
   as an alternative was deleted in `11aff8c`.)
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
