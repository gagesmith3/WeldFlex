# Routes & templates

## Route inventory

Page routes (`app.py`) — verified against the code 2026-07-28:
```
/                                   landing.html
/operator                           operator.html
/operator/admin                     admin.html   (hidden — 700ms long-press on header home button, see admin.js)
/operator/parts                     parts.html
/operator/job-history               job_history.html
/operator/faceplate                 faceplate.html   (stub — linked from admin.html's Tools card, no content yet)
/operator/calibration               calibration.html   (menu page)
/operator/jog                       jog.html
/operator/calibration/force-sensor  force_sensor.html
/operator/tcp-calibrate             tcp_calibrate.html
/operator/weld-test                 weld_test.html   (bring-up tool — runs programs/weld.lua once)
/operator/robot-diagnostics         robot_diagnostics.html
/operator/settings                  settings.html
/manager                            manager.html   (standalone shell — does not extend base.html)
```

There is **no `/operator/liberty`** and no `liberty.html` — the Liberty
experiment was removed and replaced by `job_manager.py`. Likewise there is no
`/operator/calibrate`; `calibrate.html` is orphaned (see below).

`/ui/*` endpoints follow `/ui/<feature>/<action>` (e.g.
`/ui/tcp-calibrate/enable-drag`, `/ui/job/start`, `/ui/jog/move`).
Multi-word features are hyphenated (`tcp-calibrate`, `studs-preview`), never
nested further (never `/ui/tcp/calibrate`). Live features: `connection`,
`diagnostics`, `ft`, `job`, `jog`, `manager`, `parts`, `recipes`, `settings`,
`tcp-calibrate`, `weld-test`.

`ft` is `/ui/ft/{reading,setup,zero,deactivate}`: `reading` is polled at 300 ms
by `force_sensor.html` (lbs-only Fz readout); `setup` and `zero` are wired to
that page's Initialize/Zero buttons (toast responses); `deactivate` exists but
has no UI caller — deliberate, not an orphan to build on.

`weld-test` is `/ui/weld-test/{run,stop,telemetry}` — the bring-up path for
`programs/weld.lua`, one stud at a time. Things about it that are load-bearing:

- **`run` refuses while `job.snapshot().active`.** Both this page and the job
  manager drive `ProgramLoad`/`ProgramRun`; if both owned the controller the
  manager would count cycles against line numbers from a program it never built.
- It uploads **two** files per run — a comment-stripped `weld.lua` and a
  generated harness (`lua_builder.build_weld_test_lua`) — from one temp dir.
  weld.lua alone faults on its own input contract, and the harness's `NewDofile`
  resolves at run time, so a stale copy on the controller is what would execute.
- The harness's frame globals are **parsed out of `WeldFlex.lua`**, not copied,
  so the test can never approach in a different frame than production. A missing
  one is a hard build error.
- Arming is a separate confirmed toggle using `window.confirm`, deliberately not
  `htmx:confirm` (which fires for every request in HTMX 1.9.x). Default is a dry
  run; `WELD_ARMED` unset means disarmed.
- **`run` takes three modes**: dry (default), armed (`armed=1`), and force test
  (`force_test=1`, its own button — search, hold weld force 5 s, retract, with
  weld.lua's DI1 gate reported-not-enforced and WELD/FEED unreachable). The
  Force Test button shares `#wt-params` with Run, so a leftover `armed=1` can
  ride along — **the route drops `armed` whenever `force_test` is set**, and
  `build_weld_test_lua` raises on the combination outright. Don't weaken either
  layer; weld.lua is the third (`DO_WELD_ENABLED` forced 0 in force test).
- The harness publishes weld.lua's sentinels: `WELD_RUN = 1` (upload gate — the
  controller's post-upload check executes top-level Lua, see the `fairino-sdk`
  skill) plus `WELD_ARMED` / `WELD_FORCE_TEST`. All are name-duplicated across
  the language boundary and pinned by `tests/test_lua_builder.py`.
- `telemetry` self-throttles like `current_job.html` (400 ms running / 1200 ms
  idle) because each tick costs three robot RPCs. It reports RPC-observable state
  only — **there is no SDK call that reads a Lua `print()`**, so don't describe
  this page as capturing program output. Its probe is all best-effort: DIs show
  `?` (raw `GetDI` bypass is dead on this firmware) and a force read failing
  with code 14 *while running* renders as a muted "sensor busy" note, not a
  warning — the controller refuses FT reads during `FT_FindSurface` moves
  (`FT_RPC_BUSY_CODE` in `app.py`; `weld_probe` returns `ft_err` instead of
  raising). A latched controller fault still outranks it via `fault_main`.

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

`home.html`, `partials/home_current_run.html` and `liberty.html` have all since
been deleted — earlier revisions of this file listed them as orphans. The
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
