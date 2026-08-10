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

## What's actually built, and what still isn't

As of the `WeldFlex.lua`/`weld.lua` rewrite (2026-08-03, commits `11aff8c`/
`e55a18b`): **a run now welds for real.** The cycle loop calls `weld.lua` per
stud (`NewDofile("/fruser/weld.lua", 1, 1)`), which fires the arc once its two
DI checks pass. A home approach/return also now exists at both ends of a run.
Do not describe either of these as unbuilt — that claim is stale and no longer
true against the committed code.

What is still missing:

1. **No arm/disarm gate.** `WeldFlex.lua` sets `WELD_ARMED = 1` on every stud,
   but `weld.lua` never reads it — there is no code path that disarms a run.
   `weld.lua` fires unconditionally once DI1 (stud on work) and DI0 (welder
   ready) both read high.
2. **`pause_points` is a dead field.** Every recipe carries it; nothing reads it.
   Per-stud operator waits do not exist. The per-cycle `gate_mode` is a different
   feature and does not cover this.

2 is deferred to a later refactor. 1 has no owner assigned yet — flag it rather
than assuming a "dry" or "test" mode exists anywhere in the production path.
