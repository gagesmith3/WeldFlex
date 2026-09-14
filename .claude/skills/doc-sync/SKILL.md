---
name: doc-sync
description: Bring WeldFlex's docs back in line with the code — CLAUDE.md, README.md, docs/ARCHITECTURE.md, docs/ROBOT_TELEMETRY.md, docs/weldNotes.md, and the .claude/skills/*/ set. Use when asked to sync, update, refresh or audit the docs; after landing a feature that changes behaviour an existing doc describes; when a doc and the code disagree; or when a doc calls something unbuilt, dead, missing or "not commissioned". Verifies every load-bearing claim against current code with file:line evidence before editing, rather than restating what the last session believed. Never rewrites orderofevent.md (the owner's words) or the vendor PDFs/txt dumps in docs/.
---

# /doc-sync — put the docs back in sync with the code

WeldFlex's docs carry **load-bearing factual claims** — "a run welds for real",
"`pause_points` is dead", "DI1 is stud-on-work", "force still rides CNDE",
"not commissioned". Code moves; those sentences don't. Every one of them has
been wrong at some point (the force one was false by the first run of this
skill), and each time a session got built on the false premise before anyone
noticed.

So this is not a "regenerate the docs" pass. It is a **claim audit**: find the
sentences recent work made false, prove the truth against code, and rewrite
only those. A doc that is still correct should come out of `/doc-sync`
byte-identical.

## Scope

| Invocation | Audits |
|---|---|
| `/doc-sync` | Working tree + every commit since the newest doc-touching commit |
| `/doc-sync <ref>` | `<ref>..HEAD`, plus the working tree |
| `/doc-sync <path>` | That one doc only, against current code |
| `/doc-sync --check` | Report only — find drift, change nothing |

Resolve the default range:

```bash
# newest commit that touched any synced doc = the floor
git log -1 --format='%h %ad' --date=short -- CLAUDE.md README.md docs/ARCHITECTURE.md \
  docs/ROBOT_TELEMETRY.md docs/weldNotes.md .claude/skills
git diff --stat            # uncommitted
git status --short         # untracked files are features too
git log --oneline <floor>..HEAD
```

Untracked files matter: a new `backend/templates/*.html` with no route yet is a
half-landed feature, and saying so is a legitimate sync result.

## Procedure

**1. Establish the change set.** Run the commands above. Read the *diffs*, not
just the file names — a 200-line diff that only renames a local is not a doc
event; a 3-line diff that flips a default is.

**2. Map changed code to candidate docs** with the trigger table below. Be
generous here; narrow in step 4.

**3. Sweep the invariants** — `references/invariants.md`. These are the claims
that have burned this project before, each with the exact command that settles
it. Run the whole sweep every time, even for a narrow scope: it is a handful of
greps, and it catches drift no diff mentions, because the drift is in a doc
nobody edited.

**4. Verify each candidate claim against the code.** This is the step the skill
exists for.

- Evidence is a `file:line` you actually read this session. Cite it in the report.
- **Never confirm a doc from another doc**, from a commit message, from a skill
  file, or from what you remember. Those are the things being audited.
- A claim you cannot settle from code — anything about the physical machine,
  the welder, the pendant, or what happened on hardware — is **not yours to
  rewrite**. Flag it for Gage and move on.
- Running `python -m pytest tests/test_lua_builder.py -q` (etc.) is legitimate
  evidence for a behavioural claim, and cheaper than reasoning about it.

**5. Edit, minimally.** Change the false sentence and the sentences that depend
on it. Do not reflow paragraphs, restyle tables, or "improve" prose you did not
come to fix — a large diff hides the real correction and makes the next audit
harder. Match each doc's own voice and altitude (`references/doc-map.md`).

**6. Report.** Three lists, in this order:

- **Corrected** — doc, claim, and the `file:line` that disproved it.
- **Verified still true** — the load-bearing claims you re-checked and left
  alone. This is the valuable half; it is what lets the next session trust the
  docs it reads.
- **Could not verify / needs Gage** — hardware claims, unowned gaps, anything
  ambiguous. Never silently drop these.

## Trigger table — changed code to docs to check

| Changed | Check |
|---|---|
| `programs/*.lua` | `docs/weldNotes.md` (per-phase process detail), `docs/ARCHITECTURE.md` (marker list, "How a part becomes a program"), `CLAUDE.md` (built vs. not built), `weldflex-app` gotcha 7 |
| `backend/lua_builder.py` | ARCHITECTURE "How a part becomes a program" + marker list, `weldNotes.md`, `weldflex-app` gotchas 3 and 12 |
| `backend/job_manager.py` | ARCHITECTURE "Job states" and "Normal operation, mapped to code", `weldflex-app` `references/state-and-session.md` |
| `backend/app.py` routes | `weldflex-app` `references/routes-and-templates.md` (route inventory, dead/orphaned map) |
| `backend/robot_link.py`, `robot_feed.py`, `frame_8083.py`, `robot_service.py` | **`docs/ROBOT_TELEMETRY.md` first — it is authoritative**, then `deployment-targets`, then ARCHITECTURE gap 3 |
| `backend/recipes.json` shape | ARCHITECTURE recipe model, `weldflex-app` `references/state-and-session.md`, parts-editor notes |
| `.env.example`, `deploy/rpi/.env.rpi.example` | README (the settings blocks it documents), `deployment-targets`, ROBOT_TELEMETRY env table |
| `deploy/rpi/**` | `deployment-targets` `references/rpi-kiosk-deploy.md`, README install/redeploy blocks |
| `fairino-python-sdk-main/**` | `fairino-sdk` skill, and `deployment-targets`' CNDE connect-gate history — a re-vendor silently reverts the patch |
| A gap closing anywhere | `CLAUDE.md` "What's actually built", ARCHITECTURE "Not yet implemented" — **and delete the stale "not built" sentence wherever else it lives**. This is the single most common miss. |

## Hard rules

| # | Rule |
|---|---|
| 1 | **`orderofevent.md` is read-only.** It is the intent spec in the owner's words. If the code has diverged from it, that is a finding to report, not a doc to correct. Propose wording in chat; never edit the file. |
| 2 | **Never touch the vendor dumps** — `docs/*.pdf`, `docs/*.txt`, `docs/Cobot Controller Communication Instruction.md` (empty stub). Reference material, not our docs. |
| 3 | **`docs/handoff-*.md` are dated records.** They describe what was believed on that date and are allowed to be stale. Append a dated correction line if it prevents real harm; never rewrite the body. |
| 4 | **Code beats docs beats skills.** When ARCHITECTURE and a `SKILL.md` disagree, the code decides and *both* get fixed. Skills go stale fastest — they are edited least and asserted most. |
| 5 | **No unverified date stamps.** "As of 2026-08-03" is only allowed with a commit you can name. Don't stamp today's date on a change you inferred. |
| 6 | **Don't delete a gotcha because you couldn't find the code.** Find out. A removed warning that was still true is worse than a stale one. If you genuinely can't settle it, downgrade it to "unverified as of `<commit>`" and report it. |
| 7 | **Safety claims never get softened without hardware evidence.** Nothing about arming, interlocks, DI/DO mapping or dry-run behaviour becomes more reassuring on the strength of code reading alone. Report it; let Gage confirm on the machine. |
| 8 | Auto-memory under `~/.claude/.../memory/` is out of scope — different system, different lifecycle. |

## Worked example — the drift this skill was built from

At the time this skill was written, `/doc-sync` on the working tree would have
found, and should find again if either regresses:

- **A claim that went false.** `weldflex-app` gotcha 7 says "`weld.lua` never
  reads that global — there is no disarmed/test path in the production loop."
  Untrue since the dry-run feature (`6722285`, 2026-09-02):
  `programs/weld.lua:535` reads `if WELD_ARMED ~= 1 then` and skips the arc
  pulse, and `programs/WeldFlex.lua:47-49` sets it per arm mode. The skill was
  last touched 2026-08-10 and never heard about it — while `CLAUDE.md`,
  `README.md` and `ARCHITECTURE.md` were all updated correctly. Rule 4 exists
  because of exactly this.
- **A feature nobody wrote down.** The `welder_profile` concept
  (`atlas`/`liberty`, `ARM_MODES`, the `--{{WELDER_PROFILE}}` and
  `--{{LIBERTY_COMMISSIONING}}` markers, `/operator/liberty`) spans `app.py`,
  `lua_builder.py`, `job_manager.py` and `WeldFlex.lua` and appears in **no**
  doc except one README block. ARCHITECTURE's marker discussion and the
  `weldflex-app` route inventory both predate it.

Two different shapes of drift. The sweep in `references/invariants.md` catches
the first; the trigger table catches the second. Run both.

## Reference files

| File | Load this when... |
|---|---|
| `references/doc-map.md` | Deciding *which* doc a fact belongs in, or what voice and altitude to write it in |
| `references/invariants.md` | Step 3 — the standing claim registry, with the exact command that settles each one |
