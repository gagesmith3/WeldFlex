# Routes & templates

## Route inventory

Page routes (`app.py`) — verified against the code 2026-09-09:
```
/                                   landing.html
/operator                           operator.html
/operator/admin                     admin.html   (hidden — 700ms long-press on header home button, see admin.js)
/operator/robot-web                 robot_web.html   (Admin tool, frames the controller's own web app — see `robot-web` below)
/operator/parts                     parts.html
/operator/job-history               job_history.html
/operator/single-shot               single_shot.html   (Admin tool, one weld at a saved target — see the `single-shot` section below)
/operator/calibration               calibration.html   (menu page)
/operator/jog                       jog.html
/operator/calibration/force-sensor  force_sensor.html
/operator/points                    points.html   (calibration menu — send the TCP to a taught point, see `points` below)
/operator/tcp-calibrate             tcp_calibrate.html
/operator/robot-diagnostics         robot_diagnostics.html
/operator/settings                  settings.html   (menu: 3×2 grid of calib-menu-card tiles; an href-less tile is a dimmed placeholder)
/operator/settings/wifi             settings_wifi.html      (Wi-Fi card — partials/wifi_card.html, /ui/wifi/*)
/operator/settings/hotspot          settings_hotspot.html   (hotspot card — partials/hotspot_card.html, /ui/wifi/hotspot/*)
/manager                            redirects to /manager/part-designer (see bug note below)
/manager/part-designer               manager.html (active_tab=part-designer)
/manager/settings                    manager.html (active_tab=settings)
/manager/reports                     manager.html (active_tab=reports)
```

**Bug: `/manager` is registered twice.** `app.py` has two `@app.route("/manager")`
handlers — an early one that redirects to `/manager/part-designer`, and a later
one, near the bottom of the file, that renders `manager.html` directly.
Werkzeug keeps the first-registered rule for an identical path, so the second
is dead, unreachable code; a request to bare `/manager` always gets the
redirect. Earlier revisions of this file documented the second handler's
behavior (a standalone shell not extending `base.html`) as if it were live —
it never runs. Worth deleting the dead handler rather than leaving it to
confuse the next reader, but that's a code fix, not a doc one.

**`/operator/liberty`, `liberty.html` and `/operator/faceplate` are gone**
(2026-09-14). The Liberty endurance page went with the `welder_profile` recipe
field and its `WELDFLEX_LIBERTY_*` settings; a recipe's DI check replaced both.
The Faceplate page became Single Shot. There is still no `/operator/calibrate`;
`calibrate.html` is orphaned (see below).

**`/operator/weld-test` is gone** — deleted in commit `11aff8c` (2026-08-03)
along with `weld_test.html`, `lua_builder.build_weld_test_lua`, and its
`/ui/weld-test/*` routes. `app.py` still carries a few unreferenced leftovers from it
(the `_weld_test` dict, `_weld_test_toast()`, `_start_weld_telemetry()` and the
`WELDFLEX_WELD_TEST_*_POLL_MS` settings) — dead code, not a route to build
against. **`robot_service.weld_probe()` is not among them**: the Job Manager's
telemetry sampler calls it every `JOB_TELEMETRY_INTERVAL_S` during a run, so it
is live production code. Earlier revisions of this file listed it as dead. If you need "one special Lua run with its own controls" again, the
`single-shot` feature below is the current pattern: it reuses `JobManager`
rather than a standalone runner.

`/ui/*` endpoints follow `/ui/<feature>/<action>` (e.g.
`/ui/tcp-calibrate/enable-drag`, `/ui/job/start`, `/ui/jog/move`).
Multi-word features are hyphenated (`tcp-calibrate`, `studs-preview`), never
nested further (never `/ui/tcp/calibrate`). Live features: `connection`,
`diagnostics`, `fault`, `ft`, `job`, `jog`, `manager`, `parts`, `points`,
`recipes`, `settings`, `single-shot`, `tcp-calibrate`, `wifi`.

`wifi` is `/ui/wifi/{card,connect,forget,radio-on}` for the Wi-Fi page and
`/ui/wifi/hotspot/{card,start,stop}` for the Hotspot page (`radio-on?card=hotspot`
answers with the hotspot card). Each one re-renders its page's whole card, and
errors show inside it; `partials/wifi_status.html` is the links/banners block
both cards include. `backend/wifi.py` does the work through `nmcli`. It is host-OS
code outside the robot chain, and its docstring has the rule that the robot's
eth0 profile is never touched. Connect and hotspot start run on a thread, and
the card polls itself every second while they run. Every change is refused while
a job is active. The two share one radio, so they are either/or: starting the
hotspot leaves the Wi-Fi network, and joining a network turns the hotspot off
(a failed join turns it back on). While the hotspot is on, the Wi-Fi card lists
the saved networks, since an access point cannot scan. Each page holds its own
sheet: `#wifi-modal` in `settings_wifi.html`, `#hotspot-modal` in
`settings_hotspot.html`.

`ft` is `/ui/ft/{reading,stream,inspect}` — that's the whole route set;
`setup` and `zero` don't exist as routes (an earlier revision of this file
listed them as wired to Initialize/Zero buttons — that UI is gone).
`force_sensor.html` today has exactly one action button, `/ui/ft/inspect`
(toast response), and says outright that "F/T configuration, zeroing, and
payload identification are managed from the pendant during commissioning."
`robot_service.py` still has `ft_setup()`/`ft_zero()`/`ft_deactivate()`
methods, but no `/ui/ft/*` route calls any of the three — `force_sensor.html`'s
own comment claiming `/ui/ft/deactivate` "still exists in app.py" is itself
stale; don't assume a `robot_service.py` method existing means a route calls
it. The readout itself is the one place in the app that does **not** use an
HTMX poll:

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

`robot-web` is the Admin page's window onto the FAIRINO controller's own web
app. It has no `/ui/*` routes; everything load-bearing is outside Flask:

- **It only works on the kiosk.** The controller sends
  `X-Frame-Options: SAMEORIGIN`, so the iframe points at the Pi's loopback
  nginx proxy (`deploy/rpi/nginx-robot-web.conf`, installer step 4b), which drops
  that header, strips the session cookie's IP `Domain`, proxies the app's
  `ws://<host>:9999` feed, and injects `static/js/robot_web_bridge.js`. Off the
  kiosk the page shows a direct link instead. `WELDFLEX_ROBOT_WEB_URL` overrides
  the frame URL.
- **The frame host must match the page host.** `robot_web_page()` builds the URL
  from `request.host`: the kiosk loads `localhost`, and a `127.0.0.1` frame is
  cross-site to it, so the login cookie is blocked as third-party and login
  loops.
- **Typing goes through a relay.** The bridge posts `kbd-open` to the page on
  field focus; the page focuses a hidden `data-kbd` input so `keyboard.js` opens,
  and relays each value back by `postMessage`.
- **Contenteditable cells only half work.** The bridge also opens on the
  controller's in-place table cells (its Points page's name column): it writes
  the whole value as `textContent` and replays Enter and blur on done
  (`editableTarget()`/`commitCell()`). Typing and saving work there, but
  Backspace does not delete (Gage, 2026-09-25), and the cause is unknown.
  Rename controller points from a laptop instead; see `deployment-targets`,
  installer step 4b.
- **The page has no header** (`hide_header=True` in `base.html`); a floating
  `.robot-web-home` button is the only way out.
- Run/Pause/Stop in the controller's app bypass `JobManager` — no run history,
  no cycle tracking.

`fault` is the header's fault modal: `GET /ui/fault/status` renders
`partials/fault_panel.html` and `POST /ui/fault/reset` clears the fault.
- **It has no chip of its own.** When `_fault_view()` reports `fault` or `estop`,
  `partials/connection_chips.html` turns the State chip red and wraps it in a
  button that opens `#fault-modal` (`base.html`). `static/js/fault.js` polls
  the panel once a second, only while the modal is open. Both routes are pure
  cache reads of `get_universal_state()`.
- **`unknown` is not `clear`.** When no source answers, a blank code proves
  nothing.
- **Reset is the same verb as Diagnostics → Reset Errors.** Both routes call
  `_reset_errors_result()`, and `robot.reset_errors()` refuses up front when
  `commands_available` is false or the E-stop is engaged. A 0 return means the
  request was accepted, not that the fault cleared; the next frame shows that.
- **The fault text comes from `backend/fault_codes.py`.** `describe(main, sub)`
  maps the controller's main/sub pair to the sentence the pendant and web app
  show ("Axis 3 collision fault"), transcribed from the V3.9.8 manual's
  appendix. `_fault_view()` passes its `description`, `category` and
  `resettable` to the panel, which warns when FAIRINO lists the fault as not
  resettable; `JobManager` appends the same description to the job's error. A
  pair the manual does not list still renders, as the category plus the
  sub-code. Don't key anything on `fault.label`: that is the feed's coarse 0-12
  `error_code` (`frame_8083.ERROR_CODES`), a different numbering.

`points` is `/ui/points/{plan,move,stop}` behind `/operator/points`, a card per
taught point in `app.POINTS` (a card with `taught: False` is shown disabled).
`plan` renders `partials/points_plan.html` for the confirm modal; `move`
re-plans from fresh reads and uploads a generated Lua program; `stop` goes
through `job.stop()` so a running job still finalizes. Load-bearing:

- **`backend/point_moves.py` owns the move.** Three `Lin` legs as offsets from
  `zerozero` (straight up, level, straight down), never a joint sweep, and it
  resets collision detection first because a stop mid-press skips weld.lua's
  restore. The program is branch-free straight-line code: the host plans and
  checks everything, because the upload check executes top-level Lua.
- **Every refusal happens before upload.** `_plan_point_move()` refuses an
  unknown or untaught point; `_live_move_poses()` refuses an active job,
  `commands_available` false, or an active tool/wobj other than
  `point_moves.MOVE_TOOL`/`MOVE_WOBJ`; `_check_base_keepout()` refuses a target
  or level leg inside the robot-base no-go circle (`backend/base_keepout.py`, a
  no-op unless `WELDFLEX_BASE_X_MM`/`_Y_MM`/`_KEEPOUT_MM` are all set). It is
  **not commissioned**: the frame assumption behind the pose read is unverified
  on hardware, which is why the modal shows the planned distances first.
- **The part designer's Goto (`/ui/parts/goto`) makes the same move.** It
  plans with `point_moves.plan_offset_move()` to a stud's offset from
  `zerozero` and goes through the same `_live_move_poses()` and
  `_check_base_keepout()`; its last leg is an offset `Lin` instead of a `Lin`
  to a named point. It used to be a single `PTP(zerozero)`, which swung the
  head into the arm (2026-09-25, per `ui_parts_goto`'s docstring). These two
  are the only callers of `robot.teach_point_pose()` and
  `robot.active_tool_wobj()`. The second reads the raw XML-RPC
  `GetActualTCPNum`/`GetActualWObjNum`, not the SDK's dead cached getters.

`single-shot` is the Admin page's one-stud tool (replaced `faceplate`
2026-09-14): `POST /ui/single-shot/{fire,move-position,move-home,feed}` behind
`/operator/single-shot`. `fire` welds once at a saved target through the same
`JobManager` real part jobs use. Things about it that are load-bearing:

- **It is not a separate job runner.** `JobManager.load()`/`_launch()` take a
  `kind` (`"part"` default, `"single_shot"`); a shot routes `_launch` to
  `lua_builder.build_single_shot_lua` instead of `build_weldflex_lua`, but
  reuses every downstream piece as-is — monitor thread, `CycleTracker`, run
  history, and `partials/current_job.html`. A loaded shot *is* the current job
  system-wide, and a part job and a shot can't run concurrently — same
  physical robot, same singleton run-slot.
- **Live or Dry is picked for every shot** in the page's confirm modal, with
  no default; `/ui/single-shot/fire` returns an error toast without one.
- **Its config lives in `recipes.json`, not a separate settings store.** The
  record marked `"system": "single_shot"` (found or created by
  `app._single_shot_recipe()`, never looked up by name) carries the target
  point plus `safe_z`/`part_z`/`stud_type`/`substrate`/`pressure_setting`/
  `di_check`, edited through `/ui/recipes/save` from the page's Settings
  modal. `_recipes_load` tags the old name-keyed `faceplates` record on first
  load. `app._hide_system_recipes()` drops it from every parts listing,
  `/ui/job/load` refuses it, and a save or delete by name skips it.
- **The target is entered as separate `target_x`/`target_y` fields**, in mm
  from `zerozero`, like the studs of a front-left part. A shot has no origin
  corner. The fields are separate because the on-screen number pad has no
  comma key, so a single `"X, Y"` box cannot be filled in on the kiosk.
  `/ui/recipes/save` holds both to `app.BED_MM` (0–762 mm, the part designer's
  `BED`) and refuses a bad target with an error toast instead of saving no
  target; an unparseable `studs_text` from the parts page is refused the same
  way. The modal only closes and reloads when the response carries
  `X-Recipe-Id`. `tests/test_lua_builder.py` resolves both programs' approach
  offsets to check a shot and a front-left part's stud at the same X/Y park in
  the same place, and that a back-right part's stud parks where a shot at its
  mirrored bed point does.
- **`single_shot.lua`** is structurally parallel to `WeldFlex.lua` but targets
  one point: PTP straight to the target at safe height, `weld.lua` once
  (feeding afterwards like any stud), then it stays parked. It never moves to
  `homewf`; the page's Move Home button does. The job runs under part_id
  `__single_shot__`, which matches no recipe, so shots never fold into part
  stats.

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
| `partials/status.html` + `live_status_mount` macro (`components/ui.html`, default endpoint `/ui/status`) | Neither the macro nor the partial is invoked from any template; `/ui/status` doesn't exist. | `partials/connection_chips.html` via `/ui/connection`, or `partials/diagnostics_readout.html` via `/ui/diagnostics` |
| `/operator/calibrate` + `/ui/calibrate/status\|enable-drag\|record-pin\|goto-clearance\|apply\|reset` | Unlinked since 2026-09-24 (the calibration menu is Jog, Force Sensor and Points); `calibrate.html`/`partials/calibrate_steps.html` exist and target all 6 endpoints — **none of these routes exist in `app.py` yet.** | This is the next planned feature — see `state-and-session.md` and the `fairino-sdk` skill's `coordinate-calibration.md` |

`home.html` and `partials/home_current_run.html` have since been deleted —
earlier revisions of this file listed them as orphans. (`liberty.html` and
`faceplate.html` are gone too, as of 2026-09-14 — see above.) The
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
# app.py — _ICONS = { ... }, one entry per icon name: raw SVG <path>/<circle> inner markup
_ICONS = { "home": '...', "link_2": '...', ... }

# app.py — def icon_safe(...), right after _ICONS
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
path data to `_ICONS` in `app.py`, or the icon will silently render as a
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

`kiosk_mode` is injected globally via the `inject_defaults()` context
processor, which returns the module-level `KIOSK_MODE = os.getenv("WELDFLEX_KIOSK",
"0") == "1"` constant (defined separately, near the top of `app.py`, not
computed inside `inject_defaults()` itself) and applied as
`<body class="kiosk">` on `base.html`'s `<body>` tag. All primary action buttons enforce
`min-height: 44px` at the `@media (max-width: 820px)` breakpoint. New
operator-facing UI should follow the same constraint — no scrolling, touch
targets ≥44px.

`WELDFLEX_KIOSK=1` is set in the RPi's `.env` (`deploy/rpi/.env.rpi.example`)
but unset on the Windows dev `.env` — the kiosk touch layout can't be visually
verified on a dev machine without setting it locally. See the
`deployment-targets` skill's `references/windows-dev.md`.
