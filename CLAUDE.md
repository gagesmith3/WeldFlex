# WeldFlex

Host-side control software for a **FAIRINO FR-16 cobot doing stud welding**.
Fairino's controller does the motion; WeldFlex owns everything around it — the
part library, generating the Lua program from a part, pushing it to the
controller over the vendor SDK, and running the job (state, progress, controls,
history) from a kiosk touchscreen: a 1280×800 panel at scale 1.6, so the UI
gets 800×500 CSS px.

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
- `.claude/skills/hmi-ssh/` — getting onto the production HMI over SSH and
  deploying to it. Run its `scripts/hmi_preflight.sh` before every deploy; the
  HMI's `backend/recipes.json` is its live part library and is tracked in git.
- `.claude/skills/doc-sync/` — `/doc-sync` audits every doc listed above against
  the code and corrects the claims that went stale. Run it after landing
  anything that changes what one of them asserts.

## Developing on this PC

- **Run the app without the robot:** Browser pane → `weldflex-offline`
  (`.claude/launch.json`, robot IP `192.0.2.1`, nothing answers). For the fake
  controller, start `stub-robot` then `weldflex-stub` (IP `127.0.0.1`). Plain
  `./venv/Scripts/python.exe backend/app.py` connects to the **real**
  controller named in `.env`.
- **Test:** `./venv/Scripts/python.exe -m pytest tests/ -q`. It migrates the
  tracked `backend/recipes.json` in place, so back it up first or restore it
  afterwards, and **commit by path, never `git add -A`**.
- **Hooks** (`.claude/hooks/`, wired in `.claude/settings.json`):
  `guard_robot.py` asks before any shell command that starts the app against
  `.env`'s robot or runs Python importing `robot_link`/`robot_service`/
  `fairino`/…; `check_edit.py` py_compiles edited `.py` and checks
  `programs/*.lua` block balance and the `pcall` ban.
- **Never, without the user's OK for that specific run:** a script against the
  live controller (one crashed it on 2026-07-28); any write on the HMI (see
  `hmi-ssh`); `git push`. Don't put run-mode logic in Lua.
- Personal permission tweaks go in `.claude/settings.local.json` (gitignored).

## What's actually built, and what still isn't

As of the `WeldFlex.lua`/`weld.lua` rewrite (2026-08-03, commits `11aff8c`/
`e55a18b`): **a run now welds for real.** The cycle loop calls `weld.lua` per
stud (`NewDofile("/fruser/weld.lua", 1, 1)`), which fires the arc once its two
DI checks pass. A home approach/return also now exists at both ends of a run.
Do not describe either of these as unbuilt — that claim is stale and no longer
true against the committed code.

**A run's mode is two switches, and nothing else** (2026-09-14). **Live or
Dry** is picked for every run: the parts run modal and the Single Shot confirm
have no default, and neither does `JobManager.load()`. **DI check** is always
on for customer parts: the per-part switch was removed in `9e5677e`, recipes
migrate to `di_check: true` when loaded, and a part save ignores the field. Only
Admin's Single Shot record still has a DI check toggle; off skips the DI0
welder-ready wait and both DI1 stud-on-work checks, **live runs included**. The
removed switch is archived at the `archive/di-check-ui` tag. That replaced the welder profile
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
