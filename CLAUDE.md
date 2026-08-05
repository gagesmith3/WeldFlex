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

## Two things that are not built yet

Do not write code, docs, or commit messages that assume these work:

1. **No return-to-home.** The program just ends after the last cycle.
2. **`pause_points` is a dead field.** Every recipe carries it; nothing reads it.
   Per-stud operator waits do not exist. The per-cycle `gate_mode` is a different
   feature and does not cover this.

Owner is building 1; 2 is deferred to a later refactor.
