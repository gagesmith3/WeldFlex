# WeldFlex — what it is and what it does

WeldFlex is the **host-side orchestration layer** for a FAIRINO FR-16 cobot doing
stud welding. It does not do motion control — Fairino's controller does that, and
WeldFlex talks to it over the vendor Python SDK.

WeldFlex's job is to own everything *around* the motion:

- hold the part definitions (where the studs are),
- generate the Lua program from a part,
- push that program to the controller and run it,
- own the run — state, progress, controls, history — so an operator on a kiosk
  touchscreen can run a job without touching the teach pendant.

The operator-facing intent is captured in [`orderofevent.md`](../orderofevent.md)
at the repo root; that file is the spec, this one maps it onto the code.

> **Read this before assuming a run welds anything.** As of 2026-07-28 the
> generated program moves the head to each stud and dwells — it does **not** fire
> a weld. See [Not yet implemented](#not-yet-implemented).

## The four layers

| Layer | Owns | Code |
|---|---|---|
| **Part Library** | Named parts, each an x/y stud list | `backend/recipes.json`, `/operator/parts` |
| **Job Manager** | The current run: state, cycle count, controls, history | `backend/job_manager.py` |
| **WeldFlex.lua** | The welding process on the controller | `programs/WeldFlex.lua` + `backend/lua_builder.py` |
| **Robot Software** | Motion, IO, safety — Fairino's, not ours | `backend/robot_link.py` → `backend/robot_service.py` → vendor SDK |

Strictly one-way: `app.py` → `job_manager.py` → `robot_service.py` →
`robot_link.py` → SDK. The job manager never touches `robot_link` or the SDK
directly, and no run state lives in `app.py`.

## Normal operation, mapped to code

1. **Operator selects a part** — `/operator/parts` renders the library from
   `recipes.json`.
2. **Prompted for cycle count** — the run modal in
   [`parts.html:55-83`](../backend/templates/parts.html#L55-L83).
3. **Job is loaded into the Job Manager** — `POST /ui/job/load`
   ([`app.py:479`](../backend/app.py#L479)) calls `JobManager.load()`, which
   queues the part and redirects the browser to `/operator`.
4. **Operator hits Run** — `POST /ui/job/start`. `JobManager.start()` returns
   immediately in `starting` and does the slow work (build → upload → run) on its
   own thread, so the POST never blocks the kiosk for the length of a program load.
5. **Job runs the requested cycles** — a monitor thread polls the link's cached
   snapshot every 250 ms and banks a cycle when `GetCurrentLine` crosses the
   generated program's loop/marker lines.
6. **Job completes** — a terminal state is recorded once, and the run is appended
   to `run_history.jsonl`.

Because progress is driven by the manager's own thread rather than browser
polling, **a job keeps advancing with the kiosk tab closed** — that is the
"persists no matter what page the user is on" requirement, and it is met.

## How a part becomes a program

The studs are **inlined at build time**, not read at runtime. `lua_builder.py`
substitutes five `--{{MARKER}}` lines in the `programs/WeldFlex.lua` template and
re-uploads the whole file on every run, replacing `/fruser/WeldFlex.lua` on the
controller.

This is deliberate. The same pass that emits the text returns the **line numbers**
of the loop head and the cycle-boundary dwell in the *generated* file, and those
line numbers are what the cycle detector counts against. Searching for them
afterwards would be fragile; deriving them from the build is not.

Consequences worth knowing:

- Never edit the copy on the controller. Edit `programs/WeldFlex.lua`, which is
  the only place motion parameters (`tool`, `wobj`, `speed`, `Z_CLEARANCE`) live.
- Those parameters are **hardcoded for every part** — none of them is a
  `--{{MARKER}}`, so `lua_builder.py` passes them through untouched and every
  recipe runs with the same speed and clearance. Making one per-part means adding
  a marker, a `build_weldflex_lua()` branch, a recipe field, and a `_launch()`
  hand-off.
- `tool` and `wobj` are **coordinate-frame slot ids**, not speeds — `tool` is the
  `SetToolCoord` id (1–15) the TCP calibration writes to, `wobj` the
  `SetWObjCoord` id. `speed` is the joint-speed percent passed to `PTP`. They look
  interchangeable in the file header and are not; renaming one to the other would
  silently drop the calibrated frame.
- The checked-in template is valid standalone Lua — a zero-stud, one-cycle no-op —
  so it can be syntax-checked on its own.
- Moving a marker line is safe. Deleting one raises at build time.

## Job states

`idle → queued → starting → running → completed`, plus `paused` (operator),
`gated` (holding at a cycle boundary for a part swap), and the terminal failures
`stopped`, `error`, `interrupted` (the link died mid-run).

`gated` comes from `gate_mode`, which controls what happens *between* cycles:

| Mode | Behaviour | Status |
|---|---|---|
| `none` | Runs straight through | Works |
| `pause` | Manager issues `ProgramPause` when it sees the cycle edge | **Default.** Works, but lands wherever the robot is inside the boundary dwell rather than exactly on the gate line |
| `di` | Lua blocks on `WaitDI` for a part-ready input | Built, **not commissioned** — the DI number is unknown and Python cannot read the gate back |

## Not yet implemented

These are known and intentional, not oversights. Do not write code or docs that
assume they work.

| # | Gap | Detail | Owner |
|---|---|---|---|
| 1 | **A job still does not weld** | `WeldFlex.lua`'s cycle loop is `PointsOffsetEnable → PTP → PointsOffsetDisable → WaitMs(1000)` — it never calls `programs/weld.lua`. **A run is still a complete dry-run motion driver.** `weld.lua` itself now exists (search → press → weld → hold → retract → feed) and can be exercised one stud at a time from `/operator/weld-test`, which uploads it with a generated harness; that is a bring-up path, not the production one. It is disarmed unless the caller publishes `WELD_ARMED = 1`. | Gage — wiring `weld.lua` into the cycle loop |
| 2 | **No return-to-home** | The spec ends a job by returning home. The generated program just ends after the last cycle; there is no home move and no home pose defined anywhere in the app. | Gage — fixing `WeldFlex.lua` |
| 3 | **User-entered waits are a dead field** | Every recipe carries a `pause_points: []` written at [`app.py:382`](../backend/app.py#L382), but nothing reads it — not `lua_builder.py`, not the part designer. `lua_builder._stud_rows` consumes only `x` and `y`. The per-cycle `gate_mode` is a *different* feature and does not cover this. | Deferred — wait system to be refactored later |

Gap 3 is the one most likely to mislead: the data model looks like it supports
per-stud waits and it does not.

## Where to go next

- Flask/HTMX conventions, route naming, response patterns —
  `.claude/skills/weldflex-app/`
- Calling the vendor SDK, return-shape gotchas, error codes —
  `.claude/skills/fairino-sdk/`
- Windows dev box vs. Raspberry Pi kiosk, deploy pipeline —
  `.claude/skills/deployment-targets/`
- Vendor API reference — `docs/fairino-doc-en-readthedocs-io-en-latest.pdf`
  (**gitignored** — not in a fresh clone; copy it in from the Fairino download)
