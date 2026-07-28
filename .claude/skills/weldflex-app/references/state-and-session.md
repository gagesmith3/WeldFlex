# State-dict pattern, recipe data model & run-session lifecycle

## The pattern: module dict + lock + read-modify-render

Multi-step wizard/session state is carried across stateless HTTP requests as a
module-level `dict` guarded by a `threading.Lock`. Every mutating route does
its mutation inside a fresh `with <lock>:` block, then renders a partial from
a **snapshot** taken under the same lock. `_tcp_render()` (`app.py:759-773`)
is the clean, factored example:

```python
def _tcp_render():
    with _tcp_lock:
        pts = set(_tcp_calib["points_recorded"])
        state = dict(_tcp_calib)
    return render_template("partials/tcp_calibrate_steps.html", points_recorded=pts, ...)
```

Every route that mutates `_tcp_calib` does its mutation inside its own
`with _tcp_lock:` block, then calls `_tcp_render()`. The equivalent for
`_run_session` is `_render_current_job()` (`app.py:467-476`). `_lbt_*` routes
instead repeat the snapshot inline at each of 6 call sites rather than
factoring out a helper — mild duplication, not a bug, but if you're adding a
new wizard, factor a render helper like `_tcp_render()` rather than repeating
the pattern inline.

**When building the work-object calibration feature**, add a new
`_wobj_calib`/`_wobj_lock` pair and a `_wobj_render()` helper shaped exactly
like `_tcp_calib`/`_tcp_render()` — same points-recorded-as-set,
per-step-error-key structure. See the `fairino-sdk` skill's
`coordinate-calibration.md` for the SDK calls that go inside the mutating
routes.

## State-dict inventory

| Dict | Lock | Shape | Notes |
|---|---|---|---|
| `_lbt_session` | `_lbt_lock` | `run_id, state, cycles_target, cycles_done, program, progress_line, last_line, last_cycle_ts, completed_since, started_at, launched_ts` | Persisted history in `_lbt_log` → `backend/liberty_log.json`. **Uniquely** advanced by a background daemon thread (see below), not purely request-driven. |
| `_current_job` | none | `name, started_at` | **Vestigial.** Written by `ui_run`/`ui_parts_run` but never read anywhere — the real "Current Job" UI is driven entirely by `_run_session`. Don't extend this dict; it's dead. |
| `_run_session` | `_run_lock` | `state, recipe_id, recipe_name, cycles_target, cycles_done, program, started_at, launched_ts` (+ `error_msg` when failed) | Production run session — see lifecycle below. |
| `_tcp_calib` | `_tcp_lock` | `points_recorded` (a `set`), `drag_point`, `drag_error`, `record_error`, `apply_error`, `applied`, `tcp_offset` | Working reference for the wizard pattern. |

## `_lbt_session`'s background-thread mutation

`_lbt_monitor_loop` (`app.py:160-207`, started via `_lbt_start_monitor`) is a
daemon thread that polls `robot.diagnostics()` every 250ms and mutates
`_lbt_session` under `_lbt_lock` **outside the request cycle**. This is the
only non-request-driven session state in the app. Don't reach for this pattern
casually — it exists because Liberty-test cycle counting needs to keep
advancing even between polls of the status endpoint; most new features should
stay purely request-driven like `_tcp_calib`/`_run_session`.

## Recipe data model

Storage: `backend/recipes.json`, a flat JSON array, loaded/saved via
`_recipes_load()`/`_recipes_save()` (`app.py:65-82`) under `_rec_lock`.

```json
{
  "id": "uuid4-string",
  "name": "demo",
  "studs": [{"x": 100, "y": 300}, ...],
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC",
  "times_ran": 0,
  "avg_cycle_time": null,
  "last_run": null,
  "pause_points": []
}
```

`times_ran`, `avg_cycle_time`, `last_run`, `pause_points` are written once at
creation and **never updated anywhere** — dead/aspirational fields. Don't
assume they're accurate if you read them; nothing increments or sets them
after a run. `_recipes_load()` auto-migrates any recipe missing an `id` by
assigning a fresh UUID and re-saving. `_recipes_enrich()` (`app.py:84-94`)
derives `studs_count` and a human `updated_label` for display.
`_preview_data()`/`_parse_studs()` (`app.py:96-121`) support a freeform
textarea → SVG preview as an alternative input mode to the coordinate-table UI.

## Run-session lifecycle

```
POST /ui/parts/run          → state="queued"
POST /ui/operator/run       → state="running"   (uploads studs_data + ProgramLoad/Run)
                             → state="error"      on exception, sets error_msg (not currently displayed — see error-handling-conventions.md)
POST /ui/pause               → state="paused"     (only if currently "running")
POST /ui/resume              → state="running"    (only if currently "paused")
GET  /ui/operator/current-job (polled every 1500ms by operator.html):
     if state=="running" and robot reports "stopped" for >10s:
        cycles_done += 1
        if cycles_done >= cycles_target: state="completed"
        else: re-upload studs + re-run same program for next cycle, stay "running"
        on exception mid-loop: state="error"
POST /ui/stop                → state="stopped"    (only if "running"/"paused")
```

**Architectural note**: multi-cycle advancement is entirely **poll-driven** —
it only happens inside the `GET /ui/operator/current-job` handler, which only
fires because `operator.html` keeps an `hx-trigger="load, every 1500ms"` mount
open. If no client is polling that endpoint, cycles never advance even though
the physical robot may have finished. The kiosk browser tab must stay open on
`/operator` for a multi-cycle run to progress.

### Landmine: the program-name mismatch

`ui_parts_run` (`app.py:491`) does
`program = recipe.get("program", "WeldFlex.lua")`, but **no code path ever
writes a `"program"` key onto a recipe** — every run always falls back to the
literal string `"WeldFlex.lua"`. There is **no `WeldFlex.lua` file anywhere in
`programs/`**. The actual base stud-cycle Lua program on disk is
`programs/studCycle_wf.lua` (which pulls in the data file
`robot.upload_studs_data()` generates, hardcoded filename
`studs_data_wf.lua`, matching the on-disk copy). Meanwhile `.env`/
`.env.example` set `WELDFLEX_PROGRAM_PATH=/fruser/studCycle.lua` — a *third*
spelling (though that env var is display-only, not used to select what
actually runs).

**Practical consequence**: the recipe-run flow only works today if a program
has been manually pre-uploaded to the physical robot under the exact name
`WeldFlex.lua`. This is a real landmine, not a documentation nitpick — see
`../../sdk-alignment-findings.md` for the open decision (rename the on-disk
file vs. change the fallback string vs. add a `program` field to the recipe
UI).

### Liberty test uses a different completion-detection strategy

`run_liberty()` (`robot_service.py:180-250`) is a separate, self-contained
flow with a **materially different** completion-detection strategy than
`_run_session`'s poll-and-guess-10s above: it regex-patches
`cycleCount = <N>` in the Lua source to the requested cycle count, scans the
patched source (within the `for i=1,cycleCount do...end` loop) for a
`WaitMs(500|1000)` line to compute a `progress_line` marker, uploads the
patched copy via a temp file, and returns
`{"program": uploaded_name, "progress_line": progress_line}` —
`progress_line` is what `_lbt_monitor_loop` polls `GetCurrentLine()` against
to detect cycle completion. **Don't assume this pattern generalizes** to
`_run_session` or vice versa — they're two distinct, independently-evolved
approaches to the same underlying problem (knowing when a Lua cycle finished).
