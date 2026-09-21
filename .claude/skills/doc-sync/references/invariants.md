# Invariant sweep — the claims that have been wrong before

Run all of these on every `/doc-sync`, whatever the scope. They are cheap, and
they catch the drift a diff never mentions: the stale sentence lives in a doc
nobody touched, so nothing in the change set points at it.

Each entry is a **claim**, **where it is asserted**, and the **command that
settles it**. Report each as confirmed or corrected. Commands assume repo root.

---

## 1. Line-number citations have rotted

**Claim shape:** any `file.py:NNN` or `#L55-L83` link in a doc.

**Why it's here:** all three spot-checked citations were stale when this skill
was written — `CLAUDE.md` and ARCHITECTURE both cite `app.py:382` for
`pause_points` (line 382 is now `@app.route("/")`), ARCHITECTURE cites
`app.py:479` for `POST /ui/job/load` (now a `if match:` inside another
function), and `weldflex-app` gotcha 14 cites `job_manager.py:644` (now
`self._state_map = STATE_MAP`). Line numbers move on every insertion above
them, and nothing warns you.

```bash
# every line citation in the synced docs
grep -rnoE '[A-Za-z_/]+\.(py|lua|html):[0-9]+|#L[0-9]+(-L[0-9]+)?' \
  CLAUDE.md README.md docs/ARCHITECTURE.md docs/ROBOT_TELEMETRY.md \
  docs/weldNotes.md .claude/skills/*/SKILL.md .claude/skills/*/references/*.md
```

Then `sed -n 'NNNp' <file>` each one and confirm it still shows what the doc
says it shows.

**When correcting:** prefer replacing the number with a **searchable anchor** —
a function name, a marker, a unique string — over a fresh number that will rot
again by the same mechanism. Keep the number only where the doc is pointing at a
specific line a reader must open (a markdown link into a template).

---

## 2. The weld interlock DI map

**Claim:** DI1 is stud-on-work, DI0 is welder-ready / caps at charge. They are
**not** interchangeable and were implemented backwards until 2026-07-28.

**Asserted in:** `weldNotes.md` §1, `fairino-sdk`'s
`references/io-and-force-torque.md` (the DI/DO map at the top of that file),
`weldflex-app` gotcha 10, `CLAUDE.md`'s read-first list.

```bash
grep -nE 'DI_STUD_ON_WORK|DI_WELD_READY' programs/weld.lua | head -4
grep -n 'WELDFLEX_WELD_STUD_DI\|WELDFLEX_WELD_READY_DI' backend/app.py
```

Lua and Python each hold their own copy and drift silently. Both must agree,
and both must agree with the docs. **Rule 7 applies** — never soften or reverse
this from code reading alone; if code and docs disagree here, stop and report
rather than "fixing" the doc.

---

## 3. The run mode: `WELD_ARMED`, `WELD_DI_CHECK` and the dry-run path

**Claim as of 2026-09-14:** a run's mode is exactly two switches, resolved once
by `lua_builder.RunMode` and published verbatim by both callers through their
`--{{RUN_MODE}}` marker. `WELD_ARMED` comes from the Live/Dry choice made for
every run (no default anywhere); dry runs the full
search/press/hold/retract/feed sequence without pulsing the weld trigger.
`WELD_DI_CHECK` comes from the recipe's `di_check`; `0` skips the DI0 wait and
both DI1 checks, live runs included.

```bash
grep -n 'WELD_ARMED\|WELD_DI_CHECK' programs/weld.lua programs/WeldFlex.lua programs/single_shot.lua
grep -n 'class RunMode\|ARM_MODES' backend/lua_builder.py
grep -n 'arm_mode\|di_check' backend/job_manager.py | head
```

Stale if any doc or skill still says: `weld.lua` ignores `WELD_ARMED`, or there
is no dry path (stale since `6722285`, 2026-09-02); a recipe has a
`welder_profile`, Liberty is dry-run only, or live runs require DI checks
(stale since 2026-09-14); or the Lua templates derive the mode themselves. The
**separate** open gap is that nothing confirms arming at the moment Run is
pressed on a loaded live job; picking Live in the run modal happens at load.
Don't conflate the two and don't mark that gap closed.

---

## 4. The marker set

**Claim:** ARCHITECTURE's "How a part becomes a program" describes which values
reach the Lua template through `--{{MARKER}}` substitution.

```bash
grep -oE '\-\-\{\{[A-Z_]+\}\}' programs/WeldFlex.lua | sort -u
grep -oE '\-\-\{\{[A-Z_]+\}\}' programs/single_shot.lua | sort -u
```

Compare against what the doc enumerates. New markers appear whenever a recipe
field starts reaching the program. At one point `WELDER_PROFILE`,
`LIBERTY_COMMISSIONING`, `WELD_TRIGGER_DO` and `WELD_TRIGGER_PULSE_MS` were live
in the template and absent from every doc; all four were removed 2026-09-14 in
favor of the single `RUN_MODE` marker.

Also re-confirm the two standing rules in that section: deleting a marker raises
at build time, and the checked-in template is valid standalone Lua (a zero-stud,
one-cycle no-op).

---

## 5. `pause_points` is still dead

**Claim:** every recipe carries `pause_points`, nothing reads it, per-stud
operator waits do not exist.

```bash
grep -rn 'pause_points' backend/ programs/ --include=*.py --include=*.lua
```

Written in two places in `app.py` and read nowhere. It stays a documented gap
until something actually consumes it — and if something starts to, this is a
**gap closing**, so `CLAUDE.md`'s ledger and ARCHITECTURE's table both change
together. Do not let a UI that merely *collects* waits count as the gap closing;
the test is whether `lua_builder` emits them.

---

## 6. `gate_mode` — which modes actually work

**Claim:** `none` works, `pause` is the default and holds *in the program* (a
`Pause()` the builder emits, skipped on the last cycle), `di` is built but not
commissioned because `WELDFLEX_GATE_DI` is unknown and Python cannot read the
gate back.

```bash
grep -n 'gate_mode\|GATE\b\|WELDFLEX_GATE_DI' backend/lua_builder.py backend/job_manager.py | head -20
```

Note the history that makes this easy to get wrong: gating by host-issued
`ProgramPause` was the original design, it did not stop the robot on hardware
(2026-08-06), and it now survives only as a backstop after the dwell expires.
Any doc describing the host as the thing that gates is stale.

---

## 7. Telemetry — what has and has not migrated

**Claim shape:** every doc that enumerates what has and has not moved onto the
port-8083 feed. **Do not trust the list in this file or in any doc — re-derive
it from code every sync.** The migration is incremental and each step falsifies
a sentence somewhere. As of the 2026-09-09 telemetry
hardening: program state, current line, fault codes, force, cycle counting *and
the DI and TCP-pose displays* come from the feed; the heartbeat is down to one
round trip while a frame is fresh; only Lua system variables remain on XML-RPC,
permanently, because 8083 carries none. That sentence is a snapshot, not an
invariant — the point of the check is that it keeps changing.

**Asserted in:** `docs/ROBOT_TELEMETRY.md` "Migration ledger",
ARCHITECTURE gap 3, `weldflex-app` gotchas 13 and 14, `deployment-targets`.

```bash
# which source wins, per signal — read the branch, not just the grep hit
grep -n 'feed.current_line\|snap.current_line' backend/robot_service.py
grep -n 'def ft_read' -A 12 backend/robot_service.py
# what the cycle monitor actually observes through
grep -n 'snapshot()\|get_universal_state' backend/job_manager.py | head
grep -rn 'WELDFLEX_CNDE_PORT\|WELDFLEX_STATUS_PORT' backend/ .env.example deploy/rpi/.env.rpi.example
```

Two specific traps:

- **`commands_available` versus `feed_streaming`.** XML-RPC dies for the whole
  of a force operation while the feed rides through. Only
  `commands_available` may gate a command; `feed_streaming` never gates
  anything. Any doc or code comment implying one connection state is stale and
  dangerous.
- **Which cache the cycle monitor reads.** This one has already flipped once:
  `_monitor_body` read `self._robot.snapshot()` (XML-RPC only) until `f41dd0a`
  and reads `get_universal_state()` (feed-preferred) after it. Docs written
  either side of that commit assert opposite things with equal confidence, so
  settle it from `job_manager.py` and never from surrounding prose. The
  `program_max_line` ceiling on `CycleTracker` is required regardless of
  source — a `NewDofile`'d sub-program reports its own line numbers.

---

## 8. Env vars: code, examples and README agree

```bash
diff <(grep -ohE 'WELDFLEX_[A-Z_]+' backend/*.py | sort -u) \
     <(grep -ohE 'WELDFLEX_[A-Z_]+' .env.example deploy/rpi/.env.rpi.example | sort -u)
```

Not every variable needs to be in an example file — but any variable that
**gates a whole feature** (the page 404s, the button is disabled, the endurance
run refuses) must appear in README with what it does. Known vestigial pair:
`WELDFLEX_FTP_USER` / `WELDFLEX_FTP_PASS` are set and unused (`LuaUpload` goes
over XML-RPC); don't "fix" that by documenting them as required.

---

## 9. Route inventory and orphaned templates

**Claim:** `weldflex-app`'s `references/routes-and-templates.md` lists the live
routes, the dead ones, and the orphaned templates.

```bash
grep -nE '^@app\.route' backend/app.py | wc -l
grep -nE '^@app\.route' backend/app.py
ls backend/templates backend/templates/partials
```

Cross-check three things: every `/operator/*` page route is listed; no route the
doc calls dead still exists; and every template has a route rendering it (an
untracked template with no route is a half-landed feature — report it, don't
document it as shipped).

---

## 10. The vendored SDK's CNDE connect-gate patch

**Claim:** both vendored `Robot.py` copies are patched to `if xmlrpc_ok:`
(commit `452bbfc`), the deploy-time `sed` that used to do it is gone, and there
is therefore **no safety net if the SDK is re-vendored**.

```bash
grep -n 'if xmlrpc_ok\|if cnde_ok and xmlrpc_ok' \
  fairino-python-sdk-main/windows/fairino/Robot.py \
  fairino-python-sdk-main/linux/fairino/Robot.py
grep -rn 'ExecStartPre' deploy/rpi/
```

If a fresh vendor drop reintroduced `if cnde_ok and xmlrpc_ok:`, that is not a
doc bug — that is a live regression that makes every real robot connection
report `-4`. Report it loudly before touching any prose.

---

## 11. Things the docs assert that code cannot settle

Do not attempt to verify these from the repo. If a change makes one of them
look outdated, **report it for Gage** rather than editing:

- The production display is 800×480 and the operator UI must fit without
  scrolling.
- 20 lbf press is ~89% of the controller's 0–100 N collision scale, which is
  why STAGE 2 faulted axis 3.
- `FT_GetConfig()` returns the real F/T sensor number; `FTC_SENSOR_NUM = 1` in
  `weld.lua` is a guess, and a wrong one mimics the STAGE 2 collision fault.
- Nothing can read a Lua `print()`, and a Lua `error()` does not latch a
  controller fault.
- No ad-hoc probe scripts against the live controller — one crashed it on
  2026-07-28.
- Pendant Auto Speed must be 100% or generated speeds are silently capped.
