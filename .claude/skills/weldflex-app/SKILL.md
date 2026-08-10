---
name: weldflex-app
description: What WeldFlex is for, plus the conventions for its own Flask app code — backend/app.py, backend/job_manager.py, backend/robot_service.py, backend/templates/**, backend/static/js/**. Use when asked what the app does or is supposed to do, and when adding or modifying any Flask route, HTMX partial/"/ui/<feature>/<action>" endpoint, template, module-level session-state dict, or robot_service.py wrapper method. Covers route/naming conventions (recipe-vs-part), the three response conventions (command-result toast, in-state-dict inline error, raw-status polling endpoint), the SDK-call wrapper pattern (_call/_unpack/_has_conn_error), the recipe data model, the job/run lifecycle and cycle counting, icon_safe()/_ICONS, and kiosk/touch CSS. A run now welds and returns home for real (2026-08-03) — the remaining gaps are no arm/disarm gate and pause_points still dead — and is the place to check before reviving or extending any calibration or recipe-library feature.
---

# WeldFlex Flask app — conventions

WeldFlex is a Flask + HTMX app (`backend/app.py`, `backend/robot_service.py`,
`backend/templates/**`) that drives a FAIRINO FR-16 cobot for stud welding,
running full-screen on an 800×480 kiosk touchscreen. For the SDK calls
themselves (not how this app wraps them), see the `fairino-sdk` skill.

## What the app is for

Fairino's controller does the motion. WeldFlex owns everything around it: the
part library, generating the Lua program from a part, pushing it to the
controller, and owning the run so the operator never touches the teach pendant.
The operator flow is: pick a part → enter a cycle count → the job loads into the
Job Manager → hit Run → it runs that many cycles → it completes.

**`docs/ARCHITECTURE.md` is the source of truth for intent** — the four layers,
how a part becomes a program, job states, and what is not built yet. Read it
before designing a feature; this file only covers *how to write the code*.

A run now welds and returns home for real (since 2026-08-03 — see gotcha 7).
Two gaps remain, because plenty of the code reads as if they were done
(gotchas 7 and 9 below): there is no arm/disarm gate, and `pause_points` is a
dead field.

## App shape

- **Page routes**: `/operator/<name>` (renders a full page extending
  `base.html`) and `/manager` (standalone shell, does *not* extend
  `base.html`).
- **HTMX partial/action endpoints**: `/ui/<feature>/<action>`, returning a
  `partials/*.html` fragment (or `partials/command_result.html`). Multi-word
  features are hyphenated (`tcp-calibrate`), not nested (`tcp/calibrate`).
- **Exceptions**: two flat `/ui/<verb>` routes remain (`/ui/connection`,
  `/ui/studs-preview`). The old flat run verbs (`/ui/run`, `/ui/pause`,
  `/ui/resume`, `/ui/stop`) are **gone** — running a job is now the `job`
  feature: `/ui/job/{load,start,pause,resume,continue,stop,clear,status,history}`.

## Running a job

`backend/job_manager.py` owns the current job — state, cycle count, controls and
history. `app.py`'s `/ui/job/*` routes are thin adapters over it; **no run state
lives in `app.py` any more**. Cycles advance on the manager's own monitor thread,
so a job keeps going with the kiosk tab closed. `backend/lua_builder.py` generates
`programs/WeldFlex.lua` (studs inlined, cycle count injected, marker line numbers
returned) as the single script uploaded to the controller.

Tests: `tests/` (pytest, `requirements-dev.txt`). `tools/stub_robot.py --cycle
LOOP_START:MARKER:CYCLES` scripts a `GetCurrentLine` feed for the cycle detector.

Full inventory and the dead-route/orphaned-template map:
`references/routes-and-templates.md`.

## Top gotchas

| # | Gotcha | Where |
|---|---|---|
| 1 | "recipe" (data layer) vs "part" (routes/UI) naming split — use **"part"** for any new route/UI, "recipe" only for JSON-storage helpers | `references/routes-and-templates.md` |
| 2 | `partials/current_job.html` renders the run **controls too**, not just the metrics — the buttons are state-driven, so they must arrive in the same swap as the state | `backend/templates/partials/current_job.html` |
| 3 | **`gate_mode="pause"` holds in the program, not from the host.** The gate is a `Pause()` instruction `lua_builder` emits at the gate line (skipped on the last cycle); the manager only watches for `program_state == "paused"`. Gating by host-issued `ProgramPause` was the original design and it did not stop the robot on hardware (2026-08-06) — it survives only as a backstop after the dwell expires. `gate_mode="di"` is built but **not commissioned**: `WELDFLEX_GATE_DI` is unknown, and Python cannot read the gate back (`GetDI` reads the dead CNDE cache) | `backend/lua_builder.py`, `references/state-and-session.md` |
| 4 | `icon_safe()` silently degrades to a plain circle for any unregistered icon name — no error | `references/routes-and-templates.md` |
| 5 | Two orphaned template groups remain (`partials/recipe_library.html`, `partials/status.html`) — don't build on them | `references/routes-and-templates.md` |
| 6 | `/operator/calibrate` + 6 `/ui/calibrate/*` routes are linked from `calibration.html` but **not implemented** — next planned feature | `references/state-and-session.md`, `fairino-sdk`'s `coordinate-calibration.md` |
| 7 | **A run welds for real, with no arm/disarm gate.** Since 2026-08-03, `WeldFlex.lua`'s cycle loop calls `weld.lua` per stud (`NewDofile("/fruser/weld.lua", 1, 1)`), and `weld.lua` fires the arc unconditionally once DI1 (stud on work) and DI0 (welder ready) both read high. `WeldFlex.lua` sets `WELD_ARMED = 1` on every stud, but `weld.lua` never reads that global — there is no disarmed/test path in the production loop. (The old `/operator/weld-test` page had dry/armed/force-test modes; it was deleted in `11aff8c` and none of that logic carried into `WeldFlex.lua`.) Don't describe a run as safely dry-runnable, in code comments, UI copy or commit messages | `programs/WeldFlex.lua`, `programs/weld.lua`, `docs/ARCHITECTURE.md` |
| 8 | **`JobManager` now runs more than customer parts.** `load()`/`_launch()` take a `kind` discriminator (`"part"` default, `"faceplate"`) that picks `build_weldflex_lua` vs. `build_weld_faceplate_lua` — everything downstream (monitor thread, `CycleTracker`, `partials/current_job.html`) is shared and kind-unaware. Adding a third kind means extending this branch, not forking a new manager | `backend/job_manager.py`, `references/routes-and-templates.md`'s `faceplate` section |
| 9 | **`pause_points` is a dead field** — written as `[]` on every recipe (`app.py:382`), read by nothing. `lua_builder._stud_rows` consumes only `x`/`y`. Per-stud operator waits do **not** exist; `gate_mode` is per-*cycle* and is a different feature | `backend/app.py`, `backend/lua_builder.py` |
| 10 | **The weld interlock DI numbers are duplicated in Lua and Python and drift silently.** DI1 = stud on work, DI0 = ready/caps at charge — **not** interchangeable, and they were mapped backwards until 2026-07-28. A test parses both files and asserts they agree; change them together | `programs/weld.lua`, `backend/app.py`, `fairino-sdk`'s `io-and-force-torque.md` |
| 11 | **Nothing can read a Lua `print()`.** No such call exists in the SDK — program output reaches the pendant console only. Any "watch the program" feature is limited to RPC-observable state (program state, `GetCurrentLine`, force, DI) — and a Lua `error()` doesn't latch a controller fault either, so a program that fault-retracted looks like a clean stop from Python | `docs/ROBOT_TELEMETRY.md` |
| 12 | **`GetCurrentLine` reports the sub-file's lines inside a `NewDofile`'d chunk** (live 2026-07-28). Shipped unguarded with the 2026-08-03 weld.lua hookup — a marker line comparison with no ceiling banks a phantom cycle within the first second of any weld, so `pause` mode silently never gates. **Fixed 2026-08-06**: `CycleTracker(..., program_max_line=built.program_line_count)` ignores any sample past the caller program's own length. Caught live on `weld_faceplate.lua`; don't reintroduce a `CycleTracker(...)` call site that omits `program_max_line` | `references/state-and-session.md`, `backend/job_manager.py`, `backend/lua_builder.py` |
| 13 | **"Connected" now means two different things — never gate a command on the wrong one.** The controller stops answering XML-RPC for the whole of a force operation while the port-8083 push keeps streaming (live 2026-08-03), so the channels are reported separately: `commands_available` (XML-RPC, the **only** correct gate for Run/Stop/anything that acts) vs `feed_streaming` (observation only). The `telemetry` state — amber `TELEMETRY`, detail "no commands" — is that window, and is expected, not a fault | `backend/robot_service.py`, `partials/connection_chips.html`, `docs/ROBOT_TELEMETRY.md` |
| 14 | **The job manager does not read `get_universal_state()`** — it reads `self._robot.snapshot()` (the XML-RPC-fed `ConnSnapshot`) directly, so cycle counting did *not* move onto the 8083 feed with everything else and still stalls during the force-op outage. Changing `get_universal_state()` therefore cannot fix or break cycle counting; they are separate paths | `backend/job_manager.py:644` |

## Worked example: the jog feature

`/operator/jog` + `robot_service.py`'s `jog_step()`/`jog_stop()`/`jog_pose()`
(implemented and live-tested against a disconnected-robot test client this
session) is a complete, working reference for wiring a new SDK-backed feature
into this app end to end: page route → state-free polling partial → raw
status-code JS-driven action endpoints → `robot_service.py` methods following
the standard `_call`/`_unpack` pattern. See `references/robot-service-wrapper.md`
for the wrapper-layer detail and `references/error-handling-conventions.md`
for why `/ui/jog/move`/`/ui/jog/stop` return bare status codes instead of a
rendered partial (Pattern C — a deliberate deviation, not an oversight).

**Next planned feature**: work-object 3-point calibration should mirror the
already-working TCP 4-point flow. App-side pattern:
`references/state-and-session.md`. SDK calls: `fairino-sdk`'s
`references/coordinate-calibration.md`.

## Reference files

| File | Load this when... |
|---|---|
| `docs/ARCHITECTURE.md` (repo root, not this dir) | Designing a feature, or you need to know what the app is *for* rather than how its code is written — layers, the part→program build, job states, what isn't built yet |
| `references/routes-and-templates.md` | Adding a route, naming something recipe/part, or checking whether a template is live vs. orphaned |
| `references/state-and-session.md` | Building a multi-step wizard or session flow, touching the recipe data model, or changing anything about the run lifecycle / cycle counting |
| `references/error-handling-conventions.md` | Deciding how a new route should report success/failure |
| `references/robot-service-wrapper.md` | Adding a new method to `WeldFlexRobotService` |

Audit log of gaps found during investigation: `../../sdk-alignment-findings.md`.
