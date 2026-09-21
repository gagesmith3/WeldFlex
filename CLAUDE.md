# WeldFlex

Host-side control software for a **FAIRINO FR-16 cobot doing stud welding**.
Fairino's controller does the motion; WeldFlex owns everything around it — the
part library, generating the Lua program from a part, pushing it to the
controller over the vendor SDK, and running the job (state, progress, controls,
history) from an 800×480 kiosk touchscreen.

Operator flow: pick a part → enter a cycle count → job loads into the Job
Manager → hit Run → runs that many cycles → completes.

Layers, strictly one-way:
`app.py` → `job_manager.py` → `robot_service.py` → `robot_link.py` → vendor SDK.

## Read first

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — what the app is for, how a
  part becomes a program, job states, and the current gaps. Start here.
- [docs/ROBOT_TELEMETRY.md](docs/ROBOT_TELEMETRY.md) — authoritative: which
  signal comes from which transport, and what has and hasn't moved to the
  port-8083 feed. Read before touching anything that reads robot state. The
  trap it exists to prevent: **XML-RPC dies for the whole of a force operation
  while the robot runs on**, so "connected" is now two separate facts —
  `commands_available` gates commands, `feed_streaming` never does.
- [orderofevent.md](orderofevent.md) — the intent spec in the owner's words.
- `.claude/skills/weldflex-app/` — Flask/HTMX conventions. Load before touching
  any route, template, or `robot_service.py` method.
- `.claude/skills/fairino-sdk/` — vendor SDK call reference and gotchas. Its
  `references/io-and-force-torque.md` opens with **the machine's DI/DO map** —
  read it before touching any interlock. DI1 is stud-on-work, DI0 is welder
  ready; they were implemented backwards until 2026-07-28 precisely because no
  such map existed.
- `.claude/skills/deployment-targets/` — Windows dev box vs. Raspberry Pi kiosk.
- `.claude/skills/doc-sync/` — `/doc-sync` audits every doc listed above against
  the code and corrects the claims that went stale. Run it after landing
  anything that changes what one of them asserts.

## What's actually built, and what still isn't

As of the `WeldFlex.lua`/`weld.lua` rewrite (2026-08-03, commits `11aff8c`/
`e55a18b`): **a run now welds for real.** The cycle loop calls `weld.lua` per
stud (`NewDofile("/fruser/weld.lua", 1, 1)`), which fires the arc once its two
DI checks pass. A home approach/return also now exists at both ends of a run.
Do not describe either of these as unbuilt — that claim is stale and no longer
true against the committed code.

**A run's mode is two switches, and nothing else** (2026-09-14). **Live or
Dry** is picked for every run: the parts run modal and the Single Shot confirm
have no default, and neither does `JobManager.load()`. **DI check** is saved on
the recipe, on by default; off skips the DI0 welder-ready wait and both DI1
stud-on-work checks, **live runs included**. That replaced the welder profile
(`atlas`/`liberty`) and the Liberty "dry-run only" guards, which the owner
removed deliberately on 2026-09-14. `lua_builder.RunMode` resolves both once and
`WeldFlex.lua`/`single_shot.lua` publish them verbatim. Don't reintroduce mode
logic in Lua, a mode saved on a recipe, or behavior keyed on a recipe's name.

What is still missing:

1. **Arming is chosen at load, not confirmed at Run.** The operator must tap
  Live or Dry for every run, and the job panel shows LIVE/DRY and DI OFF, but
  pressing Run on the operator page starts a loaded live job with no further
  confirmation. Dry sets `WELD_ARMED = 0` and completes search, press, hold,
  retract, and feeder advance without pulsing the weld trigger output.
2. **`pause_points` is a dead field.** Every recipe carries it; nothing reads it.
   Per-stud operator waits do not exist. The per-cycle `gate_mode` is a different
   feature and does not cover this.

2 is deferred to a later refactor. 1 has no owner assigned yet.
