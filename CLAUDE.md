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
- [orderofevent.md](orderofevent.md) — the intent spec in the owner's words.
- `.claude/skills/weldflex-app/` — Flask/HTMX conventions. Load before touching
  any route, template, or `robot_service.py` method.
- `.claude/skills/fairino-sdk/` — vendor SDK call reference and gotchas. Its
  `references/io-and-force-torque.md` opens with **the machine's DI/DO map** —
  read it before touching any interlock. DI1 is stud-on-work, DI0 is welder
  ready; they were implemented backwards until 2026-07-28 precisely because no
  such map existed.
- `.claude/skills/deployment-targets/` — Windows dev box vs. Raspberry Pi kiosk.

## Three things that are not built yet

Do not write code, docs, or commit messages that assume these work:

1. **Nothing welds.** `programs/weld.lua` now exists — search, press to force,
   pulse, hold, retract, feed — but `WeldFlex.lua`'s cycle loop still never calls
   it. The loop moves to each stud and dwells, so a *job* is still a complete
   dry-run motion driver. weld.lua is reachable only from `/operator/weld-test`,
   one stud at a time, and is disarmed unless the caller sets `WELD_ARMED = 1`.
   (`programs/weld_wf.lua`, the 0-byte placeholder, has been deleted.)
2. **No return-to-home.** The program just ends after the last cycle.
3. **`pause_points` is a dead field.** Every recipe carries it; nothing reads it.
   Per-stud operator waits do not exist. The per-cycle `gate_mode` is a different
   feature and does not cover this.

Owner is building 1 and 2; 3 is deferred to a later refactor.
