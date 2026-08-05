# Controller-side Lua — a different API from the Python SDK

Everything in `programs/*.lua` runs **inside the controller**, in a Lua
instruction set that is **not** the Python SDK. Same-looking names, different
contracts. Writing controller Lua against `Robot.py`'s docstrings is how
`weld.lua` ended up unrunnable for two live sessions.

**Source of truth:** `docs/FR Lua programmingscript.txt` (text extract, greppable)
and `docs/FRLua programming script user manual.pdf` (authoritative). The text
extract is OCR-lossy — **numeric literals in its code examples are merged**
(`FT_LinInsertion(0,50,1,0100,1)` is really `(0,50,1,0,100,1)`), so trust the
"Prototype" and "Parameter" table rows, never the example arg counts.

Grep the prototypes with:
`grep -n "^Prototype " "docs/FR Lua programmingscript.txt"`

For `weld.lua` specifically — the phase/sysvar map, force-ladder rationale,
collision-guard strategy, and preconditions — `docs/weldNotes.md` is the
current authoritative writeup and should be read first; it replaced the
inline header comments that used to live in the file itself. This file stays
focused on the controller Lua instruction set in general.

## The three that cost the most

### 1. `FT_*` return values are documented as `null` and are not

Tables 3-217 through 3-227 all say **"Return value null"** — `FT_Guard`,
`FT_Control`, `FT_SpiralSearch`, `FT_RotInsertion`, `FT_LinInsertion`,
`FT_FindSurface`, `FT_Click`, `FT_ComplianceStart/Stop`.

**Live, this firmware returns `1` from a successful `FT_FindSurface`**
(2026-07-29, watched: the torch descends, touches off, and the return is `1`).
So *both* of these fault on a search that worked:

```lua
local err = FT_FindSurface(...)
if err ~= 0 then fault() end                       -- nil ~= 0 is TRUE in Lua
if type(ret) == "number" and ret ~= 0 then ... end -- and 1 ~= 0 is also true
```

That is the whole explanation for every "found the surface and immediately
retracted" run since 2026-07-28 — the retract being watched was the program's
own fault handler.

**`1` cannot be an error code.** FAIRINO's error-code table (SDK manual §2.5)
runs `-7 … -1`, then `0` = *"Successful call"*, then jumps straight to `3` and
continues to `207`. There is **no code 1 and no code 2 anywhere in it**. (Trust
that reading of the table: it puts `14` at *"Interface execution failed"*, the
code the host already sees live from `FT_GetForceTorqueRCS` during a
force-control move.)

So bound refusals to the vendor's error space, and treat anything outside it as
"no result reported":

```lua
-- nil, 0, 1, 2 and non-numbers all mean "nothing was reported".
local function ftRefused(ret)
    if type(ret) ~= "number" then return false end
    return ret < 0 or ret >= 3
end
```

Useful codes from that table when force control misbehaves: `59` force/torque
sensor not activated, `60` sensor reference frame not switched to tool, `61`
sensor not homed, `62` sensor load not zeroed, `187` force control and impedance
control started simultaneously.

### 2. There is no force-read instruction in controller Lua

`FT_GetForceTorqueRCS` **does not exist** in the Lua manual — it is Python-SDK
only. Nothing in the Lua API returns force or torque. Consequences:

- A Lua-side "poll until force ≥ X for N ms" loop **cannot be written.**
- Force control is a **blocking overlay**, not something you sample: enable
  `FT_Control`, issue one motion/insertion instruction, disable. Every vendor
  example (Code 3-53, 3-54) has that shape.
- The **host** verifies force, over RPC, from `robot_service.weld_probe`.

The documented press-to-force composite is `FT_Control` + `FT_LinInsertion`
(manual Code 3-53 lines 17-20), which is what `programs/weld.lua` now uses.

### 3. Direction encodings differ between neighbouring instructions

| Instruction | Param | Encoding |
|---|---|---|
| `FT_FindSurface(rcs, dir, axis, lin_v, lin_a, dismax, ft)` | `dir` | **1 = positive, 2 = negative** |
| `FT_LinInsertion(rcs, ft, lin_v, lin_a, dismax, linorn)` | `linorn` | **0 = negative, 1 = positive** |

Never alias these to one shared constant. `weld.lua` keeps `FIND_DIR` and
`PRESS_DIR` separate for exactly this reason. Both are `1` for a positive tool-Z
approach, which is why the bug would stay hidden until someone flips one.

Also: `axis` is **1-indexed** (1=X, 2=Y, 3=Z), and `rcs` is 0 = tool frame,
1 = base frame.

## Talking back to the host from a running program

`print()` exists (standard Lua) but nothing can read it over RPC. `PrintMsg()`
is the vendor's own print (Table 3-12) — same problem.

Two channels actually work:

| Channel | Lua side | Host side |
|---|---|---|
| **System variables** (best) | `SetSysVarvalue(name, value)`, ids 1–20 | `GetSysVarValue(id)` — a **real RPC call** (`Robot.py:5460`), not a `robot_state_pkg` cache read |
| **Line number** | park on a dwell line unique to the site | `GetCurrentLine`, which does cross into `NewDofile`'d sub-file lines |

System variables are the only channel that keeps reporting while force control
owns the sensor and `FT_GetForceTorqueRCS` is refused with code 14. `weld.lua`'s
`pub()` writes to eight slots — 1 phase, 2 last `FT_*` return, 3/4 press
contact-Z/travel, 5 collision-guard state, 8 press force target, and **6/7 the
two weld interlock DI levels** (`SV_STUD_ON_WORK`/`SV_WELD_READY`). `app.py`
decodes the phase/fault/guard/ret codes in `_WELD_PHASES`/`_WELD_FAULT_SITES`/
`_WELD_GUARD_CODES`/`_WELD_RET_CODES`. Full slot semantics and phase-code
table: `docs/weldNotes.md` — that file is now the authoritative,
actively-maintained writeup for `weld.lua` specifically (it replaced the
file's own inline header comments), and should be read before touching
anything in that program rather than re-deriving the telemetry contract from
the source.

**DI is reported this way because Python has no working read of its own on
this firmware** (both `GetDI` routes are dead — see `io-and-force-torque.md`).
`weld.lua`'s `readDI()` calls the controller-side `GetDI(id, thread)`
instruction directly (in-process on the controller, not over RPC at all, so
none of the Python-side deadness applies) and publishes the level through
`pub()`; the host never calls `GetDI` itself, only `GetSysVarValue` on slots 6
and 7. `programs/io_monitor.lua` is a second, standalone program using the
identical mechanism with the welding stripped out, so the two interlock tiles
can be watched live without a weld test having to run. Both programs publish
`-1` ("unknown") back into those slots on a clean exit specifically because
system variables outlive the program that wrote them — a level left sitting
in a slot after the program stops is exactly how a stale reading gets
displayed as a live input (observed 2026-07-30).

Caveats: the manual spells the setter `SetSysVarvalue` (lowercase `v`) while the
SDK uses `SetSysVarValue`, so `pub()` resolves the spelling with `type(...) ==
"function"` — a check, not a call, so it is free.

The argument form is the unresolved part. The manual calls `s_var` a "system
variable **name**" (Table 3-12) and its Code 3-4 example passes an unquoted
`s_var_3`; the SDK's `SetSysVarValue(id, value)` takes `id ∈ [1,20]`
(`Robot.py:4689`). Most likely `s_var_3` is a predefined global equal to `3` and
the two agree. **Pass the slot as a bare number**: Lua's C API coerces a number
argument to a string for `luaL_checkstring`, so a number satisfies a name-taking
binding too, while the string `"s_var_1"` would throw against an id-taking one.
Since `pcall` is banned (below), "cannot throw" has to be designed in rather than
caught — `weld.lua` also keeps every `pub()` call to the same `(number, number)`
shape and makes the first one fire before any motion, so a throw lands with the
torch parked.

## The controller sandbox rejects some core Lua

`pcall` is **banned**. The post-upload check refuses the entire file:

```
lua_name:weld.lua---line_num:297---error_info:pcall is not allowed in lua file
```

(live, 2026-07-29). It is a whole-file rejection, so a single defensive wrapper
anywhere makes the program unuploadable — you cannot write controller Lua in the
"try it and swallow the error" style at all. Assume `xpcall` and possibly
`assert` are in the same list until proven otherwise.

`error`, `print`, `type`, `tostring`, `string.format` and `..` all pass the check
— the 2026-07-28 refusal reported `requireContract`'s own `error()` *string*,
which means the file had already cleared the static scan with all of those in it
and was then executed.

`app.py` blanks whole-line comments before uploading `weld.lua`
([`strip_lua_comments`](../../../../backend/lua_builder.py)), so a comment
discussing `pcall` by name never reaches the controller. `tests/test_lua_builder.py`
asserts the ban against the **stripped** text for that reason.

## Other contract notes

- `GetDI(id, thread)` returns a **bare value** (Table 3-76), not `(err, value)`.
- `WaitDI(id, status, maxtime, opt)` — `opt` 0 **stops the program** on timeout.
  That skips any retract/disarm cleanup, which is why `weld.lua` polls `GetDI`
  in a loop instead of using it.
- `SetDO(id, status, smooth, thread)` blocks; `SPLCSetDO(id, status)` does not.
- `NewDofile(path, layer, id)` + `DofileEnd()` for sub-programs. A `NewDofile`'d
  chunk **cannot see the caller's locals** — pass state through globals.
- `FT_Control`'s flattened arg list is 36 values: `flag, sensor_num, select[6],
  force_torque[6], gain[6], adj_sign, ILC_sign, max_dis, max_ang, polishRadio,
  filter_Sign, posAdapt_sign, M0, M1, B0, B1, Threshold1, Threshold2,
  adjustCoeff1, adjustCoeff2, isNoBlock` (Table 3-218). `gain` is
  `f_p, f_i, f_d, m_p, m_i, m_d`.
- `FT_Guard` (Table 3-217) is the only over-force instruction Lua has — with no
  force read, a script cannot enforce a ceiling itself. Flattened: `flag,
  tool_id, select[6], value[6], max_threshold[6], min_threshold[6]`.

## Known-bad reference in this repo

`programs/test_ft_sensor.lua` is where `weld.lua`'s `FT_FindSurface` config was
copied from, and it encodes **both** of the top two mistakes: it branches on
`err == 0` and it calls `FT_GetForceTorqueRCS`. Its `print()` output was never
readable, so it has never actually been verified to do anything. Do not treat it
as a working reference.
