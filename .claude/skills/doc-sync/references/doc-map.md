# Doc map — who owns which fact, and in what voice

A fact belongs in exactly one doc. Every extra copy is a future contradiction,
because the copies never get updated together. When you catch yourself about to
duplicate a paragraph, link instead.

## The synced set

### `CLAUDE.md` (repo root)

**Owns:** the one-paragraph orientation, the strict layer order, the "read
first" index, and the **built vs. not-built ledger**.

**Voice:** terse, directive, second-person-implied. It is instructions to an
agent, not prose for a person. It already contains lines like "Do not describe
either of these as unbuilt — that claim is stale and no longer true against the
committed code" — that imperative register is correct, keep it.

**Belongs here:** anything an agent must know in the first ten seconds or it
will do damage. A gap closing or opening. A layer changing.

**Does not belong here:** route lists, marker lists, per-phase weld detail, env
var tables. Those live downstream; CLAUDE.md links to them.

**Highest drift risk in the file:** the numbered gap list at the bottom. It is
the first thing to check on any sync and the last thing anyone remembers to
edit. Gaps also carry an owner — if a gap closes, say who closed it and in
which commit; if it opens, "Unassigned" is an honest answer.

---

### `README.md` (repo root)

**Owns:** what a human needs to *operate or install* the thing — the normal
operator flow in two lines, pendant preflight, the Flask run command, the RPi
kiosk install and redeploy blocks, and the `.env` settings a feature refuses to
run without.

**Voice:** human-facing, shop-floor. Blockquote callouts for anything that will
bite (`> **Pendant preflight:** ...`).

**Belongs here:** commands you would actually type, and the settings that gate a
feature. If a feature is disabled unless three env vars are set, README is where
that contract is written down.

**Does not belong here:** internal architecture, code paths, layer rules. README
links to ARCHITECTURE for those and should stay short enough to read standing up.

**Drift risk:** the `.env` blocks. Any new gating variable in `app.py` that is
absent from README means an operator hits a dead page with no explanation.

---

### `docs/ARCHITECTURE.md`

**Owns:** intent mapped onto code — the four layers, normal operation
step-by-step with `file:line` links, how a part becomes a program (markers,
inlining, why the line numbers come from the build), job states and `gate_mode`,
and the **"Not yet implemented"** table.

**Voice:** explanatory and opinionated. It explains *why* a design is the way it
is ("This is deliberate"), and it is comfortable calling out the fragile step.
Keep the reasoning when you edit — a sync that strips the rationale and leaves
the fact makes the doc weaker.

**Belongs here:** anything about how a part becomes a running program, and
anything about what the system is *for*. This is the doc `weldflex-app`'s skill
defers to for intent.

**Does not belong here:** how to write the Flask code (that is `weldflex-app`),
and telemetry mechanics (that is ROBOT_TELEMETRY — ARCHITECTURE summarizes in
one paragraph and links).

**Drift risk:** the "Not yet implemented" table and the marker discussion. Both
enumerate, and enumerations rot. Cross-check the table against `CLAUDE.md`'s
gap list every sync — they must not disagree.

---

### `docs/ROBOT_TELEMETRY.md`

**Owns:** everything about the wire — the three transports and their ports, why
`commands_available` and `feed_streaming` are separate facts, which module owns
which socket, which signal still rides the old path, freshness rules, and the
recovery procedure.

**Voice:** specification. Present tense, current state only. It says so itself:
"Trust this file over the skills or any inline comment that disagrees." That
authority is load-bearing — **it is the tiebreaker for all telemetry claims**,
so it has to be right before anything else gets corrected from it.

**Belongs here:** transports, ownership, staleness, recovery, and the honest
list of what has not migrated yet.

**Does not belong here:** SDK call signatures (that is `fairino-sdk`), and job
or cycle logic beyond how it reads state.

**Drift risk:** the "Migration ledger" section — called "Still on the old path"
until 2026-09-09, when it was renamed because three of its four entries had
inverted. That list only shrinks by someone doing migration work, and the person
doing it is the least likely to edit this file afterwards. Verify each entry
against code rather than assuming the list is current — force/CNDE, DI, cycle
counting and the heartbeat all moved as separate migrations, each one silently
falsifying a sentence written for the one before it.

---

### `docs/weldNotes.md`

**Owns:** the `weld.lua` sub-process in depth — the six phases and their
parameters, the fault behaviour, and how the program talks back to the host
given that `print()` and `error()` never leave the pendant.

**Voice:** technical notes. Numbered phases, named constants with their default
values, `> [!IMPORTANT]` callouts for the fault path.

**Belongs here:** anything about what happens between touching the work and
retracting. Constants and their defaults belong here *with* their units.

**Does not belong here:** the outer cycle loop (ARCHITECTURE), and host-side
job state (ARCHITECTURE / `weldflex-app`).

**Drift risk:** the constants. A default changed in `weld.lua` and not here is
the quietest possible failure — the doc still reads as authoritative and the
number is simply wrong. Grep every constant named in this doc against the
current `weld.lua` on any sync that touched `programs/`.

---

### `.claude/skills/*/SKILL.md` and their `references/`

**Own:** how to *write code* in this repo — `weldflex-app` for Flask/HTMX
conventions, `fairino-sdk` for vendor call gotchas, `deployment-targets` for the
two-target split.

**Voice:** gotcha tables, numbered, each row ending in a "where" pointer.

**These drift fastest.** They are asserted constantly and edited rarely, and
they are the only docs that state things as flat rules an agent will follow
without re-checking. A wrong row here does more damage than a wrong paragraph
anywhere else. Treat every numbered gotcha as a claim due for re-verification,
not as background.

When correcting one, keep the row number stable if you can — other files cite
them by number ("gotcha 7"). If a row is genuinely dead, replace its content and
say so in the row rather than renumbering the table.

## Read-only and out of scope

| Path | Why |
|---|---|
| `orderofevent.md` | The owner's intent spec, in his words. Divergence is a finding, not an edit. |
| `docs/*.pdf`, `docs/*.txt` | Vendor manuals and dumps. |
| `docs/Cobot Controller Communication Instruction.md` | Empty stub beside its PDF. Leave it. |
| `docs/handoff-*.md` | Dated session records. Historically accurate by definition; append a correction, never rewrite. |
| `.claude/sdk-alignment-findings.md`, `.claude/force_sensor.md`, `.claude/onlineDocs.md` | Investigation logs, not maintained docs. Cite them; don't sync them. |
| `~/.claude/.../memory/**` | Separate system. |

## Cross-doc consistency checks

Run these after editing, because a correct edit in one doc can leave a
contradiction in another:

1. `CLAUDE.md`'s gap list and ARCHITECTURE's "Not yet implemented" table do not
   contradict each other on any gap they both carry. CLAUDE.md is deliberately
   the shorter list — the gaps that will make an agent write wrong code, not
   in-progress migrations — so a differing *count* is expected; a differing
   *status* for the same gap is a finding.
2. Nothing in the skills contradicts `docs/ROBOT_TELEMETRY.md` about transports
   or about what `commands_available` versus `feed_streaming` means.
3. README's `.env` blocks name only variables that exist in `backend/*.py`, and
   every variable that gates a whole feature appears in one of them.
4. Any "as of `<date>`" line names a commit that exists in `git log`.
