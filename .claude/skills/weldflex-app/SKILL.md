---
name: weldflex-app
description: Conventions for WeldFlex's own Flask app code — backend/app.py, backend/robot_service.py, backend/templates/**, backend/static/js/**. Use when adding or modifying any Flask route, HTMX partial/"/ui/<feature>/<action>" endpoint, template, module-level session-state dict, or robot_service.py wrapper method. Covers route/naming conventions (recipe-vs-part), the three response conventions (command-result toast, in-state-dict inline error, raw-status polling endpoint), the SDK-call wrapper pattern (_call/_unpack/_has_conn_error), the recipe data model and run-session lifecycle, icon_safe()/_ICONS, and kiosk/touch CSS. Also the place to check before reviving or extending any calibration, home-page, or recipe-library feature.
---

# WeldFlex Flask app — conventions

WeldFlex is a Flask + HTMX app (`backend/app.py`, `backend/robot_service.py`,
`backend/templates/**`) that drives a FAIRINO FR-16 cobot for stud welding,
running full-screen on an 800×480 kiosk touchscreen. For the SDK calls
themselves (not how this app wraps them), see the `fairino-sdk` skill.

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
| `references/routes-and-templates.md` | Adding a route, naming something recipe/part, or checking whether a template is live vs. orphaned |
| `references/state-and-session.md` | Building a multi-step wizard or session flow, or touching the recipe data model. **Its run-session and Liberty sections are stale** — `_run_session`, `_current_job` and all `_lbt_*` state were replaced by `job_manager.py` |
| `references/error-handling-conventions.md` | Deciding how a new route should report success/failure |
| `references/robot-service-wrapper.md` | Adding a new method to `WeldFlexRobotService` |

Audit log of gaps found during investigation: `../../sdk-alignment-findings.md`.
