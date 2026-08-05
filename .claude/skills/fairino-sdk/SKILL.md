---
name: fairino-sdk
description: Reference for calling the vendored FAIRINO FR-16 Python SDK (fairino-python-sdk-main/{windows,linux}/fairino/Robot.py, byte-identical between platforms). Use when writing, reviewing, or debugging code that invokes Robot.py methods — jog/motion (StartJOG/StopJOG/GetRobotMotionDone), tool-TCP or work-object coordinate calibration (SetToolCoord/SetWObjCoord, ComputeTcp4/ComputeWObjCoord, DragTeachSwitch), program and Lua file management (Mode/ProgramLoad/ProgramRun, LuaUpload/LuaDelete), IO (SetDO/GetDI/etc.), force-torque sensor setup (FT_*), or connection/error-code handling (xmlrpc_timeout, RobotError, GetSafetyCode). Covers return-shape inconsistencies, local-cache-vs-RPC reads, and id-indexing gotchas not obvious from the official docstrings/PDF.
---

# FAIRINO Python SDK — calling conventions

Source of truth: `fairino-python-sdk-main/windows/fairino/Robot.py` and
`.../linux/fairino/Robot.py` are **byte-identical** (~18,664 lines each) and
pure Python, stdlib-only (`ctypes` is used for struct packing, not `dlopen`) —
`requirements.txt` needs no platform-specific entries for it. Each vendor drop
also ships a compiled `libfairino/Robot.*.pyd`/`.so` variant plus stray Cython
`build/lib.*` artifacts inside `fairino/`, but **this app imports neither** —
`robot_service.py`'s `_bootstrap_sdk()` puts `fairino-python-sdk-main/{windows,linux}/fairino`
on `sys.path` and does `from fairino import Robot`, always resolving the plain
`.py` source. Don't add `libfairino` to a deploy step thinking it's required.
Treat `Robot.py` as one canonical source; line numbers below apply to both. See
the `deployment-targets` skill for how platform selection and the RPi CNDE
patch history work.

This is **not a stock FAIRINO SDK checkout** — it has WeldFlex-specific patches:
Chinese debug `print()`s, a custom CNDE client (port configurable via
`WELDFLEX_CNDE_PORT`/`Robot.RPC.ROBOT_CNDE_PORT`, class default `20005` — see
the port-discrepancy note in `io-and-force-torque.md` before assuming either
20004 or 20005 is actually in effect), a `set_state_callback()` hook added for
WeldFlex's telemetry rewrite, `RPC.is_connect`/reconnect machinery, and —
critically — several `Get*` functions rewritten to read a **locally-cached
UDP/CNDE state struct** (`self.robot_state_pkg`) instead of round-tripping
over XML-RPC. These always report error code `0` regardless of the robot's
real-time state accuracy. The tell: a commented-out `# _error =
self.robot.X(...)` line sitting directly above a `return 0,
self.robot_state_pkg....` line. Every reference file below flags which
functions in its domain do this.

**Don't assume the standard `(err_code, value)` return shape.** Most methods
return `(0, payload)` on success / `(err_code, None)` on failure, but this SDK
has real exceptions to that — bare-int-only returns, inverted tuple/int
success-vs-failure shapes, 3-tuples. See the gotcha table below and
`backend/robot_service.py`'s `_unpack()` helper (documented in the
`weldflex-app` skill's `robot-service-wrapper.md`), which already normalizes
all the variants seen so far. Unpack defensively; don't assume a fixed arity.

## Top gotchas

| # | Gotcha | Detail |
|---|---|---|
| 1 | Local-cache reads (`robot_state_pkg`) still always report `err=0` regardless of whether the cache is actually filling | `GetRobotMotionDone`, `GetProgramState`, `GetRobotErrorCode`, `GetActualTCPNum`/`GetActualWObjNum`, `FT_GetForceTorqueRCS`/`Origin`, `GetDI`/`GetDO`/tool variants all still read the struct rather than round-tripping. **As of the 2026-07-29/2026-08-03 telemetry rewrite this is no longer uniformly "dead"**: `robot_link.py`'s `RobotLink` now wires the vendored SDK's `FRCNDEClient.set_state_callback()` (a hook added to both vendored `Robot.py` copies) into a callback that fills an immutable `ForceSnapshot` on every complete CNDE frame, and `robot_service.ft_read()` reads that snapshot first, falling back to the raw `FT_GetForceTorqueRCS` RPC only when it's stale. **Program state, current line and fault codes left the SDK entirely on 2026-08-03** — they are decoded from the port-8083 push (`backend/frame_8083.py` + `robot_feed.py`), because the controller stops answering XML-RPC for the duration of a force operation while the pushed frame keeps arriving. The raw XML-RPC reads into `ConnSnapshot` remain as the fallback when no fresh frame exists, and the SDK's cached getters are still never used — see `error-handling-and-connection.md`. **DI is not read from Python at all any more, cache or raw**: `weld_probe()` dropped host-side `GetDI` entirely (both the wrapped and the raw-RPC form were dead/unreliable on this firmware); the working replacement is controller-side Lua calling `GetDI` itself and publishing the level through `SetSysVarValue` (a real RPC read from the host side) — see `io-and-force-torque.md` and `controller-lua-api.md`. `docs/ROBOT_TELEMETRY.md` is the authoritative spec for all of this; the CNDE port itself has an unresolved discrepancy worth checking before trusting the force path — see that file's port note. |
| 2 | `GetRobotErrCode` does not exist | Only `GetRobotErrorCode` exists in `Robot.py`. The wrong name raises `AttributeError`. |
| 3 | Asymmetric failure shape | `GetActualJointPosDegree`/`GetActualTCPPose` return a **bare int** (no `None`) on failure, unlike most getters |
| 4 | Coordinate `id` indexing is inconsistent | `SetToolCoord`/`SetToolList` use `id ∈ [1,15]` (1-indexed); `SetWObjCoord`/`SetWObjList` use `id ∈ [0,14]` (0-indexed) |
| 5 | `StopJOG`'s `ref` = start-ref + 1 | Start refs 0/2/4/8 (joint/base/tool/workpiece) stop at 1/3/5/9. `ImmStopJOG` takes no ref at all. |
| 6 | `refFrame` for work-object compute calls is undocumented | Neither the Chinese docstring nor the English PDF enumerate its value space — every official example uses `0` (base coordinate system); treat that as the safe default |
| 7 | `LuaUpload` fails if the destination file already exists | Always `LuaDelete` first (ignore its error — file may not exist yet) before re-uploading the same filename |
| 8 | `SetAO`/`SetToolAO` take a 0–100% input | The SDK internally multiplies by `40.95` before sending — don't pre-scale to the raw 0–4095 range yourself |
| 9 | **`LuaUpload` code `-1` has two distinct sources** — read the errorStr before touching the Lua | Either the raw socket transfer on :20010 (five points in `__FileUpLoad`, nothing parsed yet) **or** the post-upload `LuaUpLoadUpdate` check refusing the file, which returns `(-1, errorStr)` — the errorStr carries the controller's actual reason. `robot_service.upload_program` surfaces it; an opaque `-1` with no detail means the transfer itself. See `program-and-file-management.md` |
| 10 | **The controller's post-upload check EXECUTES the uploaded file's top-level Lua** | Confirmed live 2026-07-28: uploading `weld.lua` ran its top level with no globals set and the check refused the file with the program's own `error()` string. Any sub-program whose top level does work needs a caller-published sentinel gate (`if WELD_RUN == 1 then ...` pattern) so a bare upload is define-only |
| 11 | Raw-RPC error code `14` ("Interface execution failed") has two live meanings | (a) a latched controller fault blocks raw reads until Cleared on the pendant/web UI; (b) `FT_GetForceTorqueRCS` returns 14 for the whole time a force-control move (`FT_FindSurface`) is executing — routine, not a fault; `weld_probe` reports it as `ft_err` instead of raising. See `error-handling-and-connection.md` |
| 12 | **Controller-side Lua is a DIFFERENT API from this SDK** — never write `programs/*.lua` against `Robot.py` docstrings | Every `FT_*` Lua instruction documents **null** as its return, and there is **no force-read instruction in Lua at all**; `FT_GetForceTorqueRCS` is Python-only. Both mistakes were live in `weld.lua` until 2026-07-29. The manual is `docs/FR Lua programmingscript.txt`. See `controller-lua-api.md` |
| 14 | **`0` is not the only non-error return** — `1` and `2` are not error codes at all | FAIRINO's error table (SDK manual §2.5) is `-7..-1`, `0` = "Successful call", then `3..207`; it skips 1 and 2 entirely. Live 2026-07-29 a **successful** `FT_FindSurface` returned `1`, and `if ret ~= 0 then fault()` turned every good search into a retract. Bound refusals to `ret < 0 or ret >= 3`. Handy codes: `14` interface execution failed, `59` F/T sensor not activated, `60` sensor frame not tool, `61` sensor not homed, `62` sensor load not zeroed |
| 13 | **`pcall` is banned by the controller's upload check** | `error_info:pcall is not allowed in lua file` — a whole-file rejection (live 2026-07-29), so one defensive wrapper anywhere makes the program unuploadable. Controller Lua cannot be written in "try it and swallow the error" style; a call that might throw must be replaced by one that can't. `error`/`print`/`type`/`tostring`/`string.format` are fine. See `controller-lua-api.md` |

Also: **bundled `example/*.py` scripts are not reliably in sync with current
`Robot.py` signatures.** A concrete instance (`TestSetCommand.py`'s
`ComputeWObjCoord`/`SetWObjCoord` calls) is documented in
`references/coordinate-calibration.md`. Cross-check the live signature/
docstring in `Robot.py` before copying any bundled example verbatim.

## How this app wraps these calls

Don't call `Robot.py` methods directly from `app.py`. WeldFlex wraps every SDK
call through `backend/robot_service.py`'s `WeldFlexRobotService` — a
single-worker executor with a hard timeout, retry-with-reconnect, and a
standard `_call()` → `_unpack()` → raise-on-nonzero pattern every new method
should follow. That wrapper convention (not the raw SDK) is documented in the
`weldflex-app` skill's `references/robot-service-wrapper.md` — read that
before adding a new `WeldFlexRobotService` method.

`backend/robot_link.py`'s `RobotLink` sits underneath that and owns the
connection itself: one daemon `_SdkWorker` thread is the only thing ever
allowed to touch the SDK object (xmlrpc.client cannot safely interleave
request/response pairs on one connection), fed by a priority queue —
`0`=operator command, `1`=core heartbeat probe, `2`=detailed telemetry — with
same-generation duplicate detail reads coalesced onto one in-flight future.
Every client is generation-tagged so a failure can only invalidate the
generation that produced it. See `error-handling-and-connection.md` for the
gotchas that model produces and `docs/ROBOT_TELEMETRY.md` for the full spec.

**Worked example**: the jog feature (`StartJOG`/`StopJOG`/`ImmStopJOG`/
`GetRobotMotionDone` wired into `robot_service.py`'s `jog_step()`/
`jog_stop()`/`jog_pose()`, plus `app.py`'s `/operator/jog` + `/ui/jog/*`
routes) is implemented and live-tested. It's a complete, working reference for
wiring a new SDK-backed feature into this app end to end — see the
`weldflex-app` skill for the full walkthrough; this skill only covers the raw
SDK calls it uses (`references/motion-and-jog.md`).

## Reference files

| File | Load this when... |
|---|---|
| `references/controller-lua-api.md` | **Writing or debugging anything in `programs/*.lua`.** The controller-side Lua instruction set — a separate API from this SDK, with its own return contracts (all `FT_*` return null), no force-read instruction, mismatched direction encodings, and the system-variable channel for reporting out of a running program |
| `references/motion-and-jog.md` | Starting/stopping jog motion, polling motion completion; also the controller-side **Lua** motion instructions (`PTP`/`Lin`) used by `programs/*.lua` |
| `references/coordinate-calibration.md` | Working on tool-TCP or work-object calibration (the next planned feature) |
| `references/program-and-file-management.md` | Loading/running Lua programs, uploading/deleting files on the robot |
| `references/io-and-force-torque.md` | Digital/analog IO, or the force-torque sensor (setup, weld-force control) |
| `references/error-handling-and-connection.md` | Handling comm failures, safety-stop state, or robot error codes |

## Upstream sources

- `docs/fairino-doc-en-readthedocs-io-en-latest.pdf` — official English manual (local copy)
- `docs/Cobot Controller Communication Instruction.pdf` — the vendor's raw
  text-based command protocol (the `/f/bIII...III/b/f` wire format on port
  20003) that `Robot.py` itself wraps. Useful for confirming a signal or error
  code exists at all independent of the Python SDK's framing of it; not
  something WeldFlex talks directly.
- `docs/collabrative_robot_8083_port_status.pdf` / `.md` — vendor doc for the
  binary status-push protocol on port 8083 (fixed `0x5A5A` frame header, 8–100ms
  period set **on the pendant**, one frame carrying `FT_data[0..5]` plus program
  state/line/fault/DI/DO/pose in a single push — no SDK class, no CNDE port to
  guess). **This is integrated as of 2026-08-03** and is not part of `Robot.py`
  at all: `backend/frame_8083.py` decodes it and `backend/robot_feed.py` owns
  the socket, both written against the vendor doc rather than the SDK. Program
  state, current line and fault codes now come from here; force and DI do not
  yet. See `docs/ROBOT_TELEMETRY.md`.
- `docs/ROBOT_TELEMETRY.md` — authoritative, current-tense spec for the
  connection/telemetry architecture (`RobotLink`, `ConnSnapshot`,
  `ForceSnapshot`, `WeldTelemetrySnapshot`, freshness rules). Written after the
  2026-07-29 telemetry rewrite; trust it over anything here that disagrees.
- `docs/weldNotes.md` — authoritative, actively-maintained technical writeup
  for `programs/weld.lua` specifically (phase/sysvar map, force ladder,
  collision-guard strategy, preconditions). Superseded the inline header
  comments that used to live in the file itself.
- `.claude/onlineDocs.md` — links to the hosted readthedocs manual, by chapter
- `../../sdk-alignment-findings.md` — dated audit log of how the gotchas above
  were discovered, and anything still open. **Not updated for the
  2026-07-29/2026-08-03 telemetry rewrite** — for CNDE/DI/force-path questions,
  `docs/ROBOT_TELEMETRY.md` and this skill are current; the audit log is not.
