# SDK & app alignment findings — audit log

This is a **dated, append-only** record of the investigation behind the
`fairino-sdk` and `weldflex-app` skills — evidence trails, discovery narrative,
and anything still undecided. It is **not** required reading for a normal
coding task; the skills carry the terse, actionable conclusions and are what
should be loaded automatically when relevant. Entries here end with a pointer
to wherever the terse version lives in a skill file (if promoted); skill
callouts end with a pointer back to the dated entry here that has the full
story.

Status updates are appended as new dated sub-entries under the original entry
— entries are never edited in place. Statuses: `open` (needs a decision or
fix), `resolved <date>`, `documented, not fixed` (convention exists, code
doesn't reflect it yet), `planned, not started`.

---

## 2026-07-23 — Program-name mismatch (`WeldFlex.lua`)

**Area**: app · **Status**: open

`ui_parts_run` (`app.py:491`) does
`program = recipe.get("program", "WeldFlex.lua")`, but no code path ever
writes a `"program"` key onto a recipe (confirmed empty across
`recipes.json`) — every recipe run falls back to the literal string
`"WeldFlex.lua"`. No file by that name exists anywhere in `programs/`. The
real base stud-cycle program on disk is `programs/studCycle_wf.lua`. `.env`/
`.env.example` set `WELDFLEX_PROGRAM_PATH=/fruser/studCycle.lua` — a third
spelling, though that variable is display-only (admin/diagnostics panels), not
used to select what actually runs.

**Evidence**: `programs/` directory listing (`feedCycle.lua`, `libertytest.lua`,
`studCycle_wf.lua`, `studs_data_wf.lua`, `testCycle.lua`, `test_ft_sensor.lua`,
`weld_wf.lua` — no `WeldFlex.lua`); `robot_service.py:266-295`
(`upload_studs_data()` hardcodes `studs_data_wf.lua`, matching the on-disk
copy `programs/studs_data_wf.lua`); `.env`/`.env.example` grep for
`WELDFLEX_PROGRAM_PATH`.

**Recommendation**: pick one canonical name and make all three references
agree. Candidates: (a) rename `studCycle_wf.lua` on disk to `WeldFlex.lua` and
re-upload to the robot; (b) change the `app.py:491` fallback string to
`"studCycle_wf.lua"`; (c) add a real `program` field to the recipe UI so it's
explicit per-recipe instead of a hardcoded fallback. No decision made yet —
today, the recipe-run flow only works if a program has been manually
pre-uploaded to the physical robot under the exact name `WeldFlex.lua`.

**Skill pointer**: `weldflex-app/references/state-and-session.md` ("Landmine:
the program-name mismatch").

---

## 2026-07-23 — "recipe" vs "part" naming inconsistency

**Area**: app · **Status**: documented, not fixed

The data layer (`recipes.json`, `_recipes_load`/`_recipes_save`,
`recipe_id`/`recipe_name` fields, `/ui/recipes/save`) calls the domain object
"recipe"; routes and UI call it "part" almost everywhere (`/operator/parts`,
`/ui/parts/*`, `parts.html`, `part_designer.js`). Cosmetic — no functional
bug, nothing breaks because of it.

**Recommendation**: standardize on "part" for routes/UI going forward
(documented as the convention in `routes-and-templates.md`); a full rename of
the storage-layer helpers was judged out of scope for a documentation-only
pass. Candidate future cleanup, not urgent.

**Skill pointer**: `weldflex-app/references/routes-and-templates.md`.

---

## 2026-07-23 — `_run_session["error_msg"]` set but never displayed

**Area**: app · **Status**: open

`error_msg` is set on the run session on failure in `ui_operator_run`
(`app.py:527-530`) and the cycle-advance branch of `ui_operator_current_job`
(`app.py:582-585`) — following the app's Pattern B (in-state-dict error key,
meant to be displayed inline by the partial). But `partials/current_job.html`
never reads `session.error_msg` — it only shows a generic
`state-badge--error` badge with no message text. An operator sees "error" with
no explanation of what went wrong.

**Recommendation**: add `{% if session.error_msg %}...{% endif %}` to
`current_job.html` next to the error badge.

**Skill pointer**: `weldflex-app/references/error-handling-conventions.md`.

---

## 2026-07-23 — Orphaned templates & routes

**Area**: app · **Status**: documented, not deleted

Three groups of dead code found by cross-referencing every `hx-get`/`hx-post`/
`href` target in `backend/templates/**` against the route list in `app.py`:

1. `home.html` + `partials/home_current_run.html` — no route renders
   `home.html`; it hx-mounts `/ui/home-current-run` and posts to
   `/ui/home/run-next`, neither of which exists. Superseded by
   `operator.html` + `partials/current_job.html` + `/ui/operator/current-job`.
2. `partials/recipe_library.html` — not included/rendered anywhere.
   References `/ui/recipes/load`, `/ui/recipes/delete`, `GET /ui/recipes` —
   none exist. Legacy precursor to `parts_editor.html`/`parts_recipe_list.html`.
3. `partials/status.html` + the `live_status_mount` macro
   (`components/ui.html:90-92`, default endpoint `/ui/status`) — neither
   invoked anywhere; `/ui/status` doesn't exist.
4. `/ui/run` (POST, `app.py:449`) — exists but never called from any
   template/JS. Superseded by `/ui/parts/run` → `/ui/operator/run`.

**Recommendation**: no urgency to delete (nothing references them, so they're
inert), but don't extend or "fix" them thinking they're live — a future
session grepping for "recipe library" or "home page" could otherwise mistake
these for the current implementation. Deleting them outright would be a
reasonable follow-up cleanup, not attempted in this documentation-only pass.

**Skill pointer**: `weldflex-app/references/routes-and-templates.md`.

---

## 2026-07-23 — `icon_safe()` silent-fallback bug

**Area**: app · **Status**: open (shrinking checklist)

`icon_safe(name, fallback="circle", ...)` (`app.py:252-262`) does
`_ICONS.get(name) or _ICONS.get(fallback) or _ICONS["circle"]` — an unknown
icon name silently renders as a plain circle instead of raising or warning.
As of this session, these icon names are referenced in templates but **not**
present in `_ICONS` (`app.py:226-250`), so they currently render as plain
circles: `clipboard`, `globe`, `loader_2`, `move_3d`, `power`, `refresh-cw`,
`repeat`, `rotate-cw`, `shield-alert`, `wifi`, `wrench` — plus their fallback
choices (`move`, `tool`, `arrow_up`), which are also missing. Concrete
instances: `calibration.html:6,16` (`move_3d`/`wrench`),
`calibrate_steps.html:98`, `settings.html`'s wifi/globe/power buttons,
`robot_diagnostics.html`'s reset-errors/reconnect buttons.

**Recommendation**: add SVG path data for each name above to `_ICONS`. This
list will shrink as icons get added — re-verify before assuming it's still
accurate; it's a snapshot from this session, not a live-checked state.

**Skill pointer**: `weldflex-app/references/routes-and-templates.md` (the
general convention — "register your icon or it silently degrades" — lives
there; this list of currently-broken names lives only here since it changes).

---

## 2026-07-23 — `GetProgramState`'s undocumented state 4

**Area**: both · **Status**: documented, not fixed

The SDK's `GetProgramState()` (`Robot.py:7164`) reads
`self.robot_state_pkg.robot_state`, a local-cache field. Its own docstring
documents values `1`=stopped/no program, `2`=running, `3`=paused — but the
underlying struct field's own comment (`Robot.py:~194`) documents a 4th value,
`4`=drag(teach) mode, that the function's docstring never mentions.
`backend/robot_service.py`'s `STATE_MAP` (`robot_service.py:58`) only maps
`0`/`1`/`2`/`3` → a read of `4` falls through to `"unknown"`. Currently
low-impact (nothing puts the robot in drag-teach mode from this app yet
except manually), but will matter once work-object calibration's drag-teach
flow is built — a status poll during calibration could show "unknown" instead
of something meaningful.

**Recommendation**: add `4: "drag_teach"` (or similar) to `STATE_MAP` when the
work-object calibration feature is built, so the diagnostics/status UI
doesn't regress to "unknown" during calibration.

**Skill pointers**: `fairino-sdk/references/program-and-file-management.md`
(SDK-side fact), `weldflex-app/references/robot-service-wrapper.md` (app-side
consequence).

---

## 2026-07-23 — Work-object 3-point calibration status

**Area**: both · **Status**: planned, not started

`/operator/calibrate` + 6 `/ui/calibrate/*` routes are linked from
`calibration.html` and have working frontend templates
(`calibrate.html`/`partials/calibrate_steps.html` already hx-target all 6
endpoints), but no backend route or `robot_service.py` method exists yet. This
session confirmed:

- The SDK flow to use: `SetWObjCoordPoint`/`ComputeWObjCoord`/`SetWObjCoord`,
  mirroring the working TCP 4-point flow
  (`SetTcp4RefPoint`/`ComputeTcp4`/`SetToolCoord`).
- **Which wobj slot to target — resolved**: `id = 2`. `programs/feedCycle.lua`,
  `programs/testCycle.lua`, and `programs/libertytest.lua` all set
  `wobj = 2`; `feedCycle.lua` explicitly comments
  `-- Work Coordinate System: WObjCoord2`. This was the open question in the
  original (lost) investigation session — now settled by direct evidence from
  the Lua programs on disk.

**Recommendation**: build following the TCP flow's exact shape (state dict +
lock + render helper in `app.py`, `wobj_*` methods in `robot_service.py`
following the standard `_call`/`_unpack` pattern).

**Skill pointers**: `fairino-sdk/references/coordinate-calibration.md`,
`weldflex-app/references/state-and-session.md`.

---

## 2026-07-23 — `force_sensor.md` superseded

**Area**: sdk · **Status**: resolved 2026-07-23

The root-level `force_sensor.md` (a prior single-question investigation note
on whether this SDK supports ~30 lbf / ~133 N constant contact force during
linear weld travel) has been merged into
`fairino-sdk/references/io-and-force-torque.md` ("Force-torque sensor — not
yet used" section), which carries forward its API pointers
(`FT_Control`/`FT_Guard`/`FT_FindSurface`/`FT_ComplianceStart`/
`ImpedanceControlStartStop`) and practical interpretation. The root file has
been reduced to a short pointer stub — see that file for the redirect. Its
original "next checks" (dry-run at low force, confirm axis/frame sign, no
live commissioning test run yet) remain **open**, carried forward verbatim
into the new reference file.

**Skill pointer**: `fairino-sdk/references/io-and-force-torque.md`.

---

## 2026-07-23 — CNDE connect-gate fix already baked into vendored SDK; deploy-time patch now dead

**Area**: both · **Status**: resolved 2026-07-23 (source), documented, not
cleaned up (deploy script)

An earlier RPi bring-up session (see the now-superseded `project-rpi-kiosk`
memory) found that the FR-16 firmware only speaks CNDE on port 20004, but this
SDK's CNDE client targets port 20005 — so the original vendored code (which
only set `RPC.is_connect = True` when **both** `cnde_ok` and `xmlrpc_ok`
succeeded) made every real robot connection report `-4`, even though XML-RPC
(the only channel this app uses) worked fine. The documented workaround at the
time was a deploy-time `sed` patch, baked into `deploy/rpi/weldflex-backend.service`'s
`ExecStartPre` line, changing `if cnde_ok and xmlrpc_ok:` → `if xmlrpc_ok:` in
the Linux SDK copy on every service start.

**What changed**: commit `452bbfc` ("fixit8", 2026-07-15) edited that
condition directly in *both* vendored `Robot.py` copies (windows/linux stayed
byte-identical) — confirmed by reading the current source
(`Robot.py:2299-2306`, now with an explanatory English comment) and by `git
log -p` showing the literal diff. The fix is source-level now, not
deploy-time. This session's `fairino-sdk/references/error-handling-and-connection.md`
was written by reading the already-fixed code, so it documents current
behavior correctly without needing correction.

**What's stale**: `weldflex-backend.service`'s `ExecStartPre` `sed` line still
searches for the pre-fix pattern, which no longer exists in the file — `sed
-i` with no match is a silent no-op (exits 0, changes nothing), so the service
still starts fine, but the line does nothing useful anymore. Left in place,
documented rather than removed (this pass is deploy-config-aware, not a deploy
script rewrite).

**Also found while investigating**: `fairino-sdk/SKILL.md` originally claimed
"only the compiled `libfairino` extension (`.pyd` vs `.so`) differs per
platform" — checked and this was misleading. Both vendor drops also ship a
compiled `libfairino/Robot.*.pyd`/`.so` extension and stray Cython
`fairino/build/lib.*` artifacts, but `robot_service.py`'s `_bootstrap_sdk()`
only ever imports the plain `fairino/Robot.py` source via `sys.path` — it
never touches `libfairino`. Corrected in place (this was a same-session
documentation error, not an app-code bug, so fixing it directly — rather than
just logging it — matched the "don't leave known-wrong docs around" spirit of
this pass).

**Recommendation**: if `weldflex-backend.service` is ever edited again for
another reason, delete the now-dead `ExecStartPre` line at the same time. Not
urgent enough to justify a standalone deploy-script change today.

**Skill pointer**: `deployment-targets/SKILL.md` ("CNDE connect-gate — patch
history"), `deployment-targets/references/rpi-kiosk-deploy.md`
("`weldflex-backend.service`" section), `fairino-sdk/SKILL.md` (corrected
`libfairino` claim).

## 2026-07-24 — F/T sensor wrongly documented as un-connectable; corrected

**What was claimed**: an earlier pass in this file's companion reference
(`fairino-sdk/references/io-and-force-torque.md`) stated that the purchased XJC
sensor could not be wired to the FR-16 at all, and that an external RS485
transmitter/conditioning module had to be sourced first. It listed three
"independent proofs": the M12 8-core end plate can't carry 14 conductors, the
state packet has only one tool analog channel, and `FT_SetConfig` enumerates
digital protocols only. The `project_force_sensor` memory and `MEMORY.md`
carried the same "BLOCKED" conclusion.

**Why it was wrong**: those three observations about the *robot* are all true
and still documented. The error was about the *sensor*. The X-6A datasheet
describes a product family; its excitation / mV-V / 14-pin-bridge spec table
covers the **analog** variant. The unit actually ordered is an
**X-6A-XD80-H28-200N-5N.m-F(RS485)** — the `F(RS485)` suffix is the output
option, meaning excitation, amplification and 6x6 decoupling are integrated in
the sensor body and it presents RS485 directly. The premise was never checked
against the full ordered part number, only against the family datasheet.

**How it surfaced**: the user supplied the complete model number, then connected
the sensor and got a live readout — which also incidentally answered the
remaining open question, since the controller's XJC driver talks to this model
despite `Robot.py:7455` documenting `company=24, device=0` as the different
`XJC-6F-D82`. `FT_SetConfig(24, 0)` in `robot_service.ft_setup()` is correct
as-is.

**What survived**: the interaction model (controller owns the sensor; RS485 ->
CNDE at 8 ms -> local struct read -> UI), the four SDK gotchas
(`FT_GetForceTorqueRCS` cannot fail, `FT_GetConfig`'s 4-vs-5 values and `+1`
offset, `FT_Control` dropping `B[1]`, `FT_SetRCS` selecting the frame), and the
`FT_SetRCS(0)` addition to `ft_setup()`. Those were traced from `Robot.py`
rather than inferred from the datasheet, which is why they held.

**Lesson**: a vendor family datasheet is not a spec for the ordered unit. Anchor
on the full ordered part number — including output-option suffixes — before
concluding anything is physically impossible. Corroborating a wrong premise
three ways produces three wrong conclusions, not confidence.

**Still open**: whether the observed live readout came via this app's Initialize
button or via FAIRINO's own pendant/web UI. If the latter, `ft_setup()`'s config
path is unexercised and the reporting frame may not be tool frame. A six-step
commissioning checklist now sits in the reference file, since a plausible-looking
readout is not evidence of a correct setup.

**Skill pointer**: `fairino-sdk/references/io-and-force-torque.md` ("The sensor
— model and connection status", "Commissioning checklist").
