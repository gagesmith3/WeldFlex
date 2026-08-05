---
name: weldflex-app
description: What WeldFlex is for, plus the conventions for its own Flask app code — backend/app.py, backend/job_manager.py, backend/robot_service.py, backend/templates/**, backend/static/js/**. Use when asked what the app does or is supposed to do, and when adding or modifying any Flask route, HTMX partial/"/ui/<feature>/<action>" endpoint, template, module-level session-state dict, or robot_service.py wrapper method. Covers route/naming conventions (recipe-vs-part), the three response conventions (command-result toast, in-state-dict inline error, raw-status polling endpoint), the SDK-call wrapper pattern (_call/_unpack/_has_conn_error), the recipe data model, the job/run lifecycle and cycle counting, icon_safe()/_ICONS, and kiosk/touch CSS. Also records the three things that are NOT built yet (nothing welds, no return-to-home, pause_points is dead) and is the place to check before reviving or extending any calibration or recipe-library feature.
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

Three gaps to keep in mind, because plenty of the code reads as if they were
done (gotchas 7–9 below): nothing actually welds, there is no return-to-home,
and `pause_points` is a dead field. The owner is building the first two.

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
| 3 | `gate_mode="di"` is built but **not commissioned**: `WELDFLEX_GATE_DI` is unknown, and Python cannot read the gate back (`GetDI` reads the dead CNDE cache). `pause` is the default | `backend/lua_builder.py` |
| 4 | `icon_safe()` silently degrades to a plain circle for any unregistered icon name — no error | `references/routes-and-templates.md` |
| 5 | Two orphaned template groups remain (`partials/recipe_library.html`, `partials/status.html`) — don't build on them | `references/routes-and-templates.md` |
| 6 | `/operator/calibrate` + 6 `/ui/calibrate/*` routes are linked from `calibration.html` but **not implemented** — next planned feature | `references/state-and-session.md`, `fairino-sdk`'s `coordinate-calibration.md` |
| 7 | **A run does not weld.** `programs/weld.lua` now exists (search → press → weld → hold → retract → feed) and has run live from `/operator/weld-test` (2026-07-28), but `WeldFlex.lua`'s cycle loop still never calls it — the loop is move + `WaitMs`. Three weld-test modes, all one stud at a time: dry run (`WELD_ARMED = 0`), **force test** (`WELD_FORCE_TEST = 1` — search, hold 20 lbf for 5 s, retract; DI1 reported not enforced; DO0 unreachable *and* forced off; can never be armed, enforced at builder, route and Lua), and armed. Don't describe a run as welding, in code comments, UI copy or commit messages | `programs/WeldFlex.lua`, `programs/weld.lua`, `docs/ARCHITECTURE.md` |
| 8 | **No return-to-home.** The generated program just ends after the last cycle; no home pose is defined anywhere in the app | `programs/WeldFlex.lua` |
| 9 | **`pause_points` is a dead field** — written as `[]` on every recipe (`app.py:382`), read by nothing. `lua_builder._stud_rows` consumes only `x`/`y`. Per-stud operator waits do **not** exist; `gate_mode` is per-*cycle* and is a different feature | `backend/app.py`, `backend/lua_builder.py` |
| 10 | **The weld interlock DI numbers are duplicated in Lua and Python and drift silently.** DI1 = stud on work, DI0 = ready/caps at charge — **not** interchangeable, and they were mapped backwards until 2026-07-28. A test parses both files and asserts they agree; change them together | `programs/weld.lua`, `backend/app.py`, `fairino-sdk`'s `io-and-force-torque.md` |
| 11 | **Nothing can read a Lua `print()`.** No such call exists in the SDK — program output reaches the pendant console only. Any "watch the program" feature is limited to RPC-observable state (program state, `GetCurrentLine`, force, DI) — and a Lua `error()` doesn't latch a controller fault either, so a program that fault-retracted looks like a clean stop from Python | `backend/templates/weld_test.html` |
| 12 | **`GetCurrentLine` reports the sub-file's lines inside a `NewDofile`'d chunk** (live 2026-07-28). Hooking weld.lua into `WeldFlex.lua`'s cycle loop will alias the job manager's marker-line cycle counting — weld.lua's line numbers overlap the parent's markers. Solve this before gotcha 7's hookup, not after | `references/state-and-session.md`, `backend/job_manager.py` |
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
