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
Chinese debug `print()`s, a custom CNDE client on port 20005, `RPC.is_connect`/
reconnect machinery, and — critically — several `Get*` functions rewritten to
read a **locally-cached UDP/CNDE state struct** (`self.robot_state_pkg`)
instead of round-tripping over XML-RPC. These always report error code `0`
regardless of the robot's real-time state accuracy. The tell: a commented-out
`# _error = self.robot.X(...)` line sitting directly above a
`return 0, self.robot_state_pkg....` line. Every reference file below flags
which functions in its domain do this.

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
| 1 | Local-cache reads always report `err=0` | `GetRobotMotionDone`, `GetProgramState`, `GetRobotErrorCode`, `GetActualTCPNum`/`GetActualWObjNum`, `FT_GetForceTorqueRCS`/`Origin`, `GetDI`/`GetDO`/tool variants — see per-domain files |
| 2 | `GetRobotErrCode` does not exist | Only `GetRobotErrorCode` exists in `Robot.py`. The wrong name raises `AttributeError`. |
| 3 | Asymmetric failure shape | `GetActualJointPosDegree`/`GetActualTCPPose` return a **bare int** (no `None`) on failure, unlike most getters |
| 4 | Coordinate `id` indexing is inconsistent | `SetToolCoord`/`SetToolList` use `id ∈ [1,15]` (1-indexed); `SetWObjCoord`/`SetWObjList` use `id ∈ [0,14]` (0-indexed) |
| 5 | `StopJOG`'s `ref` = start-ref + 1 | Start refs 0/2/4/8 (joint/base/tool/workpiece) stop at 1/3/5/9. `ImmStopJOG` takes no ref at all. |
| 6 | `refFrame` for work-object compute calls is undocumented | Neither the Chinese docstring nor the English PDF enumerate its value space — every official example uses `0` (base coordinate system); treat that as the safe default |
| 7 | `LuaUpload` fails if the destination file already exists | Always `LuaDelete` first (ignore its error — file may not exist yet) before re-uploading the same filename |
| 8 | `SetAO`/`SetToolAO` take a 0–100% input | The SDK internally multiplies by `40.95` before sending — don't pre-scale to the raw 0–4095 range yourself |

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
| `references/motion-and-jog.md` | Starting/stopping jog motion, polling motion completion |
| `references/coordinate-calibration.md` | Working on tool-TCP or work-object calibration (the next planned feature) |
| `references/program-and-file-management.md` | Loading/running Lua programs, uploading/deleting files on the robot |
| `references/io-and-force-torque.md` | Digital/analog IO, or the force-torque sensor (setup, weld-force control) |
| `references/error-handling-and-connection.md` | Handling comm failures, safety-stop state, or robot error codes |

## Upstream sources

- `docs/fairino-doc-en-readthedocs-io-en-latest.pdf` — official English manual (local copy)
- `.claude/onlineDocs.md` — links to the hosted readthedocs manual, by chapter
- `../../sdk-alignment-findings.md` — dated audit log of how the gotchas above were discovered, and anything still open
