# Routes & templates

## Route inventory

Page routes (`app.py`) — verified against the code 2026-09-09:
```
/                                   landing.html
/operator                           operator.html
/operator/admin                     admin.html   (hidden — 700ms long-press on header home button, see admin.js)
/operator/parts                     parts.html
/operator/job-history               job_history.html
/operator/faceplate                 faceplate.html   (maintenance weld page — see the `faceplate` section below)
/operator/calibration               calibration.html   (menu page)
/operator/jog                       jog.html
/operator/calibration/force-sensor  force_sensor.html
/operator/tcp-calibrate             tcp_calibrate.html
/operator/robot-diagnostics         robot_diagnostics.html
/operator/settings                  settings.html
/operator/liberty                   liberty.html   (endurance test — .env-gated, see below)
/manager                            manager.html   (standalone shell — does not extend base.html)
```

**`/operator/liberty` and `liberty.html` exist again** — not the old removed
experiment, but the Liberty endurance page built on the `welder_profile`
discriminator, paired with `POST /ui/liberty/start`. It runs a saved
Liberty-profile recipe and is disabled unless `WELDFLEX_LIBERTY_LIVE_ENABLED`,
`WELDFLEX_LIBERTY_TRIGGER_DO` and `WELDFLEX_LIBERTY_TRIGGER_PULSE_MS` are all
set in `.env`. Earlier revisions of this file said no such route or template
existed; that is no longer true. There is still no `/operator/calibrate`;
`calibrate.html` is orphaned (see below).

**`/operator/weld-test` is gone** — deleted in commit `11aff8c` (2026-08-03)
along with `weld_test.html`, `lua_builder.build_weld_test_lua`, and its
`/ui/weld-test/*` routes. `app.py` still carries a few unreferenced leftovers from it
(the `_weld_test` dict, `_weld_test_toast()`, `_start_weld_telemetry()` and the
`WELDFLEX_WELD_TEST_*_POLL_MS` settings) — dead code, not a route to build
against. **`robot_service.weld_probe()` is not among them**: the Job Manager's
telemetry sampler calls it every `JOB_TELEMETRY_INTERVAL_S` during a run, so it
is live production code. Earlier revisions of this file listed it as dead. If you need "one special Lua run with its own controls" again, the
`faceplate` feature below is the current pattern: it reuses `JobManager` and
`partials/current_job.html` rather than a standalone runner.

`/ui/*` endpoints follow `/ui/<feature>/<action>` (e.g.
`/ui/tcp-calibrate/enable-drag`, `/ui/job/start`, `/ui/jog/move`).
Multi-word features are hyphenated (`tcp-calibrate`, `studs-preview`), never
nested further (never `/ui/tcp/calibrate`). Live features: `connection`,
`diagnostics`, `faceplate`, `ft`, `job`, `jog`, `manager`, `parts`, `recipes`,
`settings`, `tcp-calibrate`.

`ft` is `/ui/ft/{reading,stream,inspect,setup,zero,deactivate}`. `setup` and
`zero` are wired to `force_sensor.html`'s Initialize/Zero buttons (toast
responses); `deactivate` exists but has no UI caller — deliberate, not an orphan
to build on. The readout itself is the one place in the app that does **not**
use an HTMX poll:

- `reading` renders `partials/ft_reading.html` and now raises rather than
  falling back to `get_universal_state()` when force is unavailable, so a stale
  cache renders the error partial instead of a plausible-looking number.
- `stream` is the same partial over Server-Sent Events — 10 Hz, capped at four
  concurrent streams by a module-level `BoundedSemaphore` (a fifth client gets
  `429` + `Retry-After`), self-terminating after 30 s with `retry: 2000` so the
  browser reconnects on its own.
- `backend/static/js/ft_live.js` drives it, and falls back to polling `reading`
  with `fetch` when `EventSource` is missing or the stream errors. It closes the
  stream on `visibilitychange`/`pagehide` and restores the page's server-rendered
  "no fresh reading" markup rather than leaving a frozen value on screen.
- `force_sensor.html` therefore no longer imports `htmx_mount`; it server-renders
  the stale state into `#ft-readout` and lets the script take over.

This is affordable **only because `robot.ft_read()` is a pure cache read**. If
anything behind `/ui/ft/reading` ever issues an RPC again, 10 Hz becomes 10
robot round trips a second per viewer. See `docs/ROBOT_TELEMETRY.md`,
"Host-side discipline".

`faceplate` is `POST /ui/faceplate/load` — queues a maintenance weld run for
shop fixture faceplates through the same `JobManager` real part jobs use.
Things about it that are load-bearing:

- **It is not a separate job runner.** `JobManager.load()`/`_launch()` gained a
  `kind` discriminator (`"part"` default, `"faceplate"`); a faceplate job routes
  `_launch` to `lua_builder.build_weld_faceplate_lua` instead of
  `build_weldflex_lua`, but reuses every downstream piece as-is — monitor
  thread, `CycleTracker`, gate handling, run history, and
  `partials/current_job.html`'s Run/Pause/Resume/Continue/Stop buttons. The
  page embeds that partial via `htmx_mount('operator-current-job-mount', ...)`
  — the **same hardcoded mount id** `operator.html` uses, which is what makes
  the shared control panel work with zero changes to the partial. Consequence:
  a loaded faceplate job *is* the current job system-wide (visible on the
  operator home page too), and a part job and a faceplate job can't run
  concurrently — same physical robot, same singleton run-slot.
- **Its config lives in `recipes.json`, not a separate settings store.** A
  reserved recipe record named `faceplates` (found/created by
  `app._faceplate_recipe()`) carries the single target point plus
  `safe_z`/`part_z`/`stud_type`/`substrate`/`pressure_setting` — the same
  fields a part recipe has — and is edited through the existing
  `/ui/recipes/save` endpoint, `faceplate.html`'s own form. It is filtered out
  of every normal-facing parts listing (`app._hide_faceplate_recipe()`, applied
  in `parts()` and `/ui/manager/parts-list`) so it can only be reached through
  `/operator/faceplate`, not run through the ordinary part pipeline.
- **`weld_faceplate.lua`** (`programs/weld_faceplate.lua` +
  `lua_builder.build_weld_faceplate_lua`) is structurally parallel to
  `WeldFlex.lua` but targets one fixed point. It never approaches `homewf`
  before the loop starts — it goes straight to the target and stays there for
  every cycle — but it does return home once, after the loop closes
  (including a fault-break), same edge-only shape as `WeldFlex.lua`'s home
  handling just without the starting approach. It also holds DO1 high through
  the inter-cycle gate instead of
  `weld.lua`'s normal 1 s pulse — the operator manually feeds the next
  faceplate while the program is paused (`gate_mode="pause"`, the only mode
  this path uses), then presses Continue, and the next cycle clears DO1 before
  moving. `weld.lua`'s own built-in feed pulse is suppressed via a new
  `WELD_SKIP_FEED = 1` sentinel the faceplate program publishes — `WeldFlex.lua`
  never sets it, so real part runs are unaffected (pinned by
  `tests/test_lua_builder.py`).

**Flat exceptions** — only two remain: `/ui/connection` and `/ui/studs-preview`.
The old flat run verbs (`/ui/run`, `/ui/pause`, `/ui/resume`, `/ui/stop`) and
`/ui/parts/run` are **gone**. Running a job is the `job` feature:
`/ui/job/{load,start,pause,resume,continue,stop,clear,status,history}`.

## "recipe" vs "part" naming

The data layer calls it **recipe** (`recipes.json`, `_recipes_load`/
`_recipes_save`, `recipe_id`/`recipe_name` form fields, `/ui/recipes/save`),
but routes/UI call it **part** almost everywhere: `/operator/parts`,
`/ui/parts/delete`, `/ui/parts/run`, `/ui/manager/parts-list`,
`/ui/manager/part-points`, `parts.html`, `parts_editor.html`,
`part_designer.js`. Only the save endpoint kept the `recipes` name.

**Convention going forward**: use **"part"** for any new route or UI element;
keep "recipe" only for the JSON-storage helper functions/fields. This is a
documented convention, not a completed rename — the existing inconsistency is
still in the code (see the audit log).

## Dead / orphaned — don't build on these

| Item | Status | Build on this instead |
|---|---|---|
| `partials/recipe_library.html` | Not included/rendered anywhere. References `/ui/recipes/load`, `/ui/recipes/delete`, `GET /ui/recipes` — none exist. | `parts.html` + `partials/parts_editor.html` + `partials/parts_recipe_list.html` |
| `partials/status.html` + `live_status_mount` macro (`components/ui.html:90-92`, default endpoint `/ui/status`) | Neither the macro nor the partial is invoked from any template; `/ui/status` doesn't exist. | `partials/connection_chips.html` via `/ui/connection`, or `partials/diagnostics_readout.html` via `/ui/diagnostics` |
| `/operator/calibrate` + `/ui/calibrate/status\|enable-drag\|record-pin\|goto-clearance\|apply\|reset` | Linked from `calibration.html`; `calibrate.html`/`partials/calibrate_steps.html` exist and target all 6 endpoints — **none of these routes exist in `app.py` yet.** | This is the next planned feature — see `state-and-session.md` and the `fairino-sdk` skill's `coordinate-calibration.md` |

`home.html` and `partials/home_current_run.html` have since been deleted —
earlier revisions of this file listed them as orphans. (`liberty.html` was
deleted too, but a new one now backs `/operator/liberty` — see above.) The
current job panel is `partials/current_job.html`, mounted from
`operator.html:10` via `htmx_mount(..., '/ui/job/status', 'load', 'innerHTML')`;
the partial then carries its own adaptive poll trigger, so that mount only fires
the first paint.

The `.home-*` CSS in `operator.css` is **not** all dead: `.home-nav-grid` /
`.home-nav-card` / `.home-nav-icon` are live (emitted by the `nav_card` macro,
used in `operator.html`'s nav panel) and `.home-btn` is the header home button.
Only `.home-body`, `.home-hero` and `.home-nav-panel` went with `home.html`.

## `icon_safe()` / `_ICONS`

```python
# app.py:226-250 — 22 entries of raw SVG <path>/<circle> inner markup
_ICONS = { "home": '...', "link_2": '...', ... }

# app.py:252-262
def icon_safe(name, fallback="circle", width=14, height=14, class_=""):
    paths = _ICONS.get(name) or _ICONS.get(fallback) or _ICONS["circle"]
    ...
app.jinja_env.globals["icon_safe"] = icon_safe
```

Registered as a Jinja global, used directly (`{{ icon_safe('play', ...) }}`)
or via `components/ui.html` macros (`action_button`, `icon_link`,
`status_chip`, `metric_card`, `nav_card`) which all hardcode `fallback='circle'`.

**`icon_safe()` never errors on an unknown name — it silently falls back to a
plain circle.** When adding a new `icon=` reference in a template, add its SVG
path data to `_ICONS` in `app.py:226`, or the icon will silently render as a
circle with no warning. (The audit log has the current list of already-broken
icon names found this session — check it before assuming an icon works.)

## Kiosk / touch CSS

`backend/static/css/operator.css` targets an 800×480 production touchscreen:
```css
/* 800x480 kiosk display — compact header, touch targets, no-scroll layouts */
@media (max-width: 820px) {
  .btn { min-height: 44px; padding: 10px 14px; }
  .estop-btn { min-height: 44px; }
  ...
}
/* Operator main page — sized for 800×480 production touchscreen */
.operator-main-body { display: grid; grid-template-columns: 4fr 1fr; ... }
.kiosk, .kiosk * { cursor: none !important; }
```

`kiosk_mode` is injected globally via `inject_defaults()` (`app.py:437-447`,
`KIOSK_MODE = os.getenv("WELDFLEX_KIOSK", "0") == "1"`) and applied as
`<body class="kiosk">` in `base.html:12`. All primary action buttons enforce
`min-height: 44px` at the `@media (max-width: 820px)` breakpoint. New
operator-facing UI should follow the same constraint — no scrolling, touch
targets ≥44px.

`WELDFLEX_KIOSK=1` is set in the RPi's `.env` (`deploy/rpi/.env.rpi.example`)
but unset on the Windows dev `.env` — the kiosk touch layout can't be visually
verified on a dev machine without setting it locally. See the
`deployment-targets` skill's `references/windows-dev.md`.
