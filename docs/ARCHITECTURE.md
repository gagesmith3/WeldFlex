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

> **A live run welds for real.** As of the 2026-08-03 rewrite (commits
> `11aff8c`/`e55a18b`) the generated program calls `programs/weld.lua` per
> stud, which fires the arc once its two DI checks pass, or without them when
> the recipe's DI check is off. Live or Dry is picked for every run. A dry run
> follows the same search, press, hold, retract, and feeder sequence, but sets
> `WELD_ARMED = 0` so it never pulses the weld trigger output.

## The four layers

| Layer | Owns | Code |
|---|---|---|
| **Part Library** | Named parts, each an x/y stud list measured from one bed corner | `backend/recipes.json`, `/operator/parts` |
| **Job Manager** | The current run: state, cycle count, controls, history | `backend/job_manager.py` |
| **WeldFlex.lua** | The welding process on the controller | `programs/WeldFlex.lua` + `backend/lua_builder.py` |
| **Robot Software** | Motion, IO, safety — Fairino's, not ours | `backend/robot_link.py` → `backend/robot_service.py` → vendor SDK |

Strictly one-way: `app.py` → `job_manager.py` → `robot_service.py` →
`robot_link.py` → SDK. The job manager never touches `robot_link` or the SDK
directly, and no run state lives in `app.py`.

## Normal operation, mapped to code

1. **Operator selects a part** — `/operator/parts` renders the library from
   `recipes.json`.
2. **Prompted for cycle count and Live or Dry** — the `#run-modal` block in
   [`parts.html`](../backend/templates/parts.html). Neither mode is
   preselected; Run stays disabled until one is tapped.
3. **Job is loaded into the Job Manager** — `POST /ui/job/load` in
   [`app.py`](../backend/app.py) calls `JobManager.load()`, which queues the
   part and redirects the browser to `/operator`.
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

Step 5 used to be the fragile one and no longer is. Cycle counting read
`GetCurrentLine` over XML-RPC, and the controller stops answering XML-RPC for
the whole of a force operation — so a cycle boundary falling inside that window
could be missed. Since `f41dd0a` the monitor reads the consolidated
`get_universal_state()`, which takes the line from the pushed port-8083 feed
that rides through the outage. What remains is that a cycle boundary is still a
*line number* the host has to recognise rather than a count the program
publishes outright. `docs/ROBOT_TELEMETRY.md` is the authoritative spec for all
of this.

## How a part becomes a program

The studs are **inlined at build time**, not read at runtime. `lua_builder.py`
substitutes named `--{{MARKER}}` lines in the `programs/WeldFlex.lua` template and
re-uploads the whole file on every run, replacing `/fruser/WeldFlex.lua` on the
controller.

This is deliberate. The same pass that emits the text returns the **line numbers**
of the loop head and the cycle-boundary dwell in the *generated* file, and those
line numbers are what the cycle detector counts against. Searching for them
afterwards would be fragile; deriving them from the build is not.

Consequences worth knowing:

- Never edit the copy on the controller. Edit `programs/WeldFlex.lua`, which is
  the only place motion parameters (`tool`, `wobj`, `speed`, `Z_CLEARANCE`) live.
- Recipe values such as speed and clearance are emitted through markers. When a
  part enables Dynamic Speed Compensation, the builder additionally emits a
  speed and possible dwell for each non-first stud-to-stud move. DSC never
  changes home, first-stud approach, descent, or return-home speed, and it stays
  unavailable until a machine-specific dry-run timing calibration is accepted.
  The pendant's Auto Speed is a global multiplier/cap over those percentages;
  set it to 100% before calibrating or running DSC, or even a generated 100%
  leg will be limited below its intended speed.
- **The run mode is emitted the same way, through one marker.** A run's mode is
  two switches, resolved once by `lua_builder.RunMode`: `arm_mode` (Live or
  Dry, chosen for every run, with no default at any layer) and `di_check`
  (saved on the recipe, default on). `--{{RUN_MODE}}` expands to `WELD_ARMED`
  and `WELD_DI_CHECK` above the cycle loop, and neither caller template derives
  or changes them. DI check off skips the DI0 welder-ready wait and both DI1
  stud-on-work checks, **live runs included**. That was the owner's decision on
  2026-09-14, which also removed the `atlas`/`liberty` welder profile, its
  dry-only guards, and the configurable trigger output (now fixed in `weld.lua`
  at DO0 for 250 ms).
- **Single Shot uses the same machinery.** `JobManager.load(kind="single_shot")`
  builds `programs/single_shot.lua` with `build_single_shot_lua`: one cycle,
  one target from the `"system": "single_shot"` record in `recipes.json`, no
  home moves, and the same `RUN_MODE` marker. `weld.lua` feeds after the shot.
- **A stud is stored the way the part is measured, and inlined the way the
  robot needs it.** Each part saves an `origin_corner`, the bed corner it is
  tooled against, and its X/Y run inward from that corner so they are never
  negative. Before inlining, `lua_builder` resolves them through
  `backend/part_origin.py` into offsets from the one taught point, `zerozero`,
  at the bed's front-left. A right corner mirrors X against `WELDFLEX_BED_X_MM`
  and a back corner mirrors Y against `WELDFLEX_BED_Y_MM`; both default to the
  nominal 762 mm. Front-left studs are inlined unchanged. The other three corners
  are computed, not taught, so they are only as accurate as the measured
  stop-to-stop distances and how square wobj 2 sits to the bed edges; teaching a
  point at each corner is the likely next step. Mirroring the studs is safe
  where mirroring a frame would not be: a user frame has to stay right-handed
  with +Z up, but a stud is a point welded with the tool vertical, and DSC only
  uses the distances between studs. `JobManager.load` resolves the studs too,
  so a stud that would flip across the bed is refused at load, not at Run.
- `WeldFlex.lua` applies each stud position through
  `PointsOffsetEnable(0, ...)`, so percentage-mode `Lin` calls must use
  `Lin(point, speed, -1, 0, 0)`. Its final `0` means no *inline* offset; it is
  not a speed setting. Do not set it to `1` unless the full inline offset
  argument list follows.
- `tool` and `wobj` are **coordinate-frame slot ids**, not speeds — `tool` is the
  `SetToolCoord` id (1–15) the TCP calibration writes to, `wobj` the
  `SetWObjCoord` id. `speed` is the requested motion percentage passed to the
  generated `Lin` moves. They look interchangeable in the file header and are
  not; renaming one to the other would silently drop the calibrated frame.
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
| `pause` | The **program pauses itself** — `lua_builder._gate_rows` emits a `Pause(PAUSE_GATE_CODE)` at the gate line, skipped after the last cycle. The host only watches for the paused state and offers Continue | **Default.** A host-issued `ProgramPause` is now only the backstop `job_manager._gate` sends if the program has not held by the end of the dwell — gating *by* `ProgramPause` did not reliably stop the robot on hardware |
| `di` | Lua blocks on `WaitDI` for a part-ready input | Built, **not commissioned** — the DI number is unknown and Python cannot read the gate back |

## Not yet implemented

These are known and intentional, not oversights. Do not write code or docs that
assume they work.

| # | Gap | Detail | Owner |
|---|---|---|---|
| 1 | **Arming is chosen at load, not confirmed at Run** | The run modal and the Single Shot confirm require a tap on Live or Dry for every run, and the job panel shows LIVE/DRY and DI OFF for the whole job. Pressing Run on the operator page still starts a loaded live job with no further confirmation. Dry sets `WELD_ARMED = 0` and runs the same motion/process sequence without pulsing the weld trigger output. | Unassigned |
| 2 | **User-entered waits are a dead field** | Every recipe carries a `pause_points: []` — written in two places in [`app.py`](../backend/app.py), read nowhere — not `lua_builder.py`, not the part designer. `lua_builder._stud_rows` consumes only `x` and `y`. The per-cycle `gate_mode` is a *different* feature and does not cover this. | Deferred — wait system to be refactored later |
| 3 | **The telemetry cutover is essentially done** | Program state, current line, fault codes, force, cycle counting **and now the DI and TCP-pose displays** come from the port-8083 push; CNDE survives only as a force fallback, so a dead CNDE port no longer means dead force. The heartbeat is down to one round trip while a frame is fresh. What is left is structural, not a migration: Lua system variables (the phase code and press diagnostics) have no feed equivalent and stay on XML-RPC permanently. | Gage — see `docs/ROBOT_TELEMETRY.md` |

Gap 2 is the one most likely to mislead: the data model looks like it supports
per-stud waits and it does not.

**Return-to-home is now built** (was gap 2 here as of 2026-07-28): a home
approach runs before the cycle loop starts and a home return runs after the
last cycle, both gated by `WeldFlex.lua`'s `USE_HOME_MOVE` flag. Do not
describe this as missing.

## Where to go next

- How WeldFlex talks to the controller — transports, which signal comes from
  where, freshness rules, recovery — `docs/ROBOT_TELEMETRY.md` (authoritative)
- Flask/HTMX conventions, route naming, response patterns —
  `.claude/skills/weldflex-app/`
- Calling the vendor SDK, return-shape gotchas, error codes —
  `.claude/skills/fairino-sdk/`
- Windows dev box vs. Raspberry Pi kiosk, deploy pipeline —
  `.claude/skills/deployment-targets/`
- Vendor API reference — `docs/fairino-doc-en-readthedocs-io-en-latest.pdf`
  (**gitignored** — not in a fresh clone; copy it in from the Fairino download)
