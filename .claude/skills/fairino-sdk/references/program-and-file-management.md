# Program & Lua file management

## Running a program

| Call | Location | Notes |
|---|---|---|
| `Mode(self, state)` | `Robot.py:2822` | `state`: `0`=auto, `1`=manual. Bare int. |
| `ProgramLoad(self, program_name)` | `Robot.py:7027` | e.g. `"/fruser/movej.lua"` — `/fruser/` is a fixed path prefix. Bare int. |
| `ProgramRun(self)` | `Robot.py:7075` | No params. **`GetSafetyCode()`-gated** — returns `99` instead of running if a safety stop is latched. Bare int. |
| `ProgramPause(self)` / `ProgramResume(self)` / `ProgramStop(self)` | `Robot.py:7098` / `7119` / `7142` | No params, bare int. `ProgramResume` is also `GetSafetyCode()`-gated. |

**Sequencing**: official PDF §2.4.10.10's example does `Mode(0)` →
`ProgramLoad(path)` → `ProgramRun()` with **no sleep** between `Mode(0)` and
`ProgramLoad`. `backend/robot_service.py`'s `run_program()` adds a defensive
`time.sleep(2)` after `Mode(0)` — a live-tested safety margin beyond what the
SDK itself documents as required. Keep that sleep as house practice even
though it's not SDK-mandated.

## Reading program state

- **`GetProgramState(self)`** — `Robot.py:7164`. The **SDK method itself** is a
  local-cache read, not RPC (`return 0, self.robot_state_pkg.robot_state`; the
  real RPC call is commented out just above it), always error `0`. **Don't
  call the SDK method** — as of the telemetry rewrite, `robot_link.py`'s core
  heartbeat (`_read_program_state`) sources this through a three-tier
  fallback instead, tried in order: (1) the CNDE struct's `program_state`
  field, but only when the CNDE receiver's own `_robot_state_run_flag` shows
  it's actually streaming (`source="cnde"` in `ConnSnapshot`); (2) raw XML-RPC
  `client.robot.GetProgramState()` (`source="rpc"`) — a real round trip,
  cached as unsupported (`source` stays `"rpc"`-eligible) only if the
  controller returns a method-not-found fault; (3) the same dead local-cache
  read the SDK method itself does, as a last resort (`source="cache"`, always
  `0`/stale). Check `ConnSnapshot.program_state_source` to know which tier
  actually produced a given `program_state_raw` value — `"cache"` means the
  same "always 0" problem as calling the SDK method directly. Docstring
  documents values `1`=stopped/no program, `2`=running, `3`=paused — **but**
  the underlying struct field comment (`Robot.py:~194`) documents a 4th value,
  `4`=drag(teach) mode, that `GetProgramState`'s own docstring never mentions.
  If you poll this while the robot is in drag-teach mode, expect `4`, not one
  of the documented three.
- **`GetCurrentLine(self)`** — `Robot.py:7050`. Real RPC call. Returns
  `(0, line_num)` / `(err, None)`. Used for line-based progress tracking (see
  the `weldflex-app` skill's liberty-test completion-detection pattern).
  **Inside a `NewDofile`'d chunk it reports the *sub-file's* line numbers**
  (observed live 2026-07-28: line 262 = `weld.lua`'s `searchForStud` while the
  weld-test harness was the loaded program), with nothing in the value saying
  which file it refers to. Any consumer comparing it against a parent file's
  marker lines — the job manager's cycle counter — will alias once the parent
  calls `NewDofile`; see the `weldflex-app` skill's cycle-counting notes. It
  also keeps answering **while motion executes**, including during
  `FT_FindSurface` (unlike FT reads — see `error-handling-and-connection.md`
  on code 14).

## Lua file upload/delete

- **`LuaUpload(self, filePath)`** — `Robot.py:9579`. **Not decorated** with
  `@log_call`/`@xmlrpc_timeout` — it delegates to a private `__FileUpLoad`
  helper with its own reconnect wait. Genuinely heavyweight and blocking:
  XML-RPC `FileUpload(fileType, file_name)` handshake → raw TCP socket to
  **port 20010** → sends a custom `/f/b<10-digit-size><md5>` header → streams
  the file in 2MB chunks → sends `/b/f` trailer → **sleeps 0.5s** → reads the
  `SUCCESS` ack. Budget several seconds for this call (`robot_service.py` uses
  a 30s timeout for it).
  - **Fails outright if the destination file already exists** (per official
    PDF §2.4.10.14: `errorStr` documents "lua file exists error"). Always call
    `LuaDelete` first and swallow its error (the file may not exist yet)
    before re-uploading the same filename — this is exactly what
    `robot_service.py`'s `upload_program()`/`upload_studs_data()` do.
  - **Return-shape deviation**: on success returns a **bare int**; on failure
    of the post-upload `LuaUpLoadUpdate` step, returns a **2-tuple**
    `(tmp_error, errorStr)` — the reverse of the SDK's usual
    "tuple-on-success, bare-int-on-failure" pattern seen elsewhere. **Never
    discard that errorStr**: it carries the controller's actual reason
    (`lua_name:...---line_num:N---error_info:...`). `robot_service.upload_program`
    unpacks it and `_upload_hint()` puts it in the raised message — adding that
    was what cracked the `weld.lua` refusal on 2026-07-28.
  - **`-1` has two distinct sources — read the detail before touching the Lua.**
    `-1` is `RobotError.ERR_OTHER` (`:570`). `__FileUpLoad` (`9477-9539`)
    returns it bare from **five raw-socket points** on :20010 (refused
    `FileUpload` RPC, failed connect, short `send()`, non-`"SUCCESS"` reply) —
    nothing parsed yet. But `LuaUpLoadUpdate` failure *also* surfaces as `-1`,
    with the errorStr attached. An opaque `-1` with no errorStr is the
    transfer; a `-1` with one is the controller refusing the file's content.
  - **The post-upload check EXECUTES the file's top-level Lua.** Confirmed live
    2026-07-28: uploading `weld.lua` ran its top level with no globals set, hit
    its own contract `error()`, and the upload was refused with that exact
    string. Consequence: a sub-program whose top level does anything needs a
    caller-published sentinel (`weld.lua`'s `if WELD_RUN == 1 then` gate;
    the harness publishes `WELD_RUN = 1`) so a bare upload is define-only.
    `tests/test_lua_builder.py` pins the sentinel on both sides.
  - **The size theory is dead.** The 2026-07-28 `-1` on the 13 KB `weld.lua`
    was the executes-top-level refusal above, not a size limit — the stripped
    ~6 KB file uploads and runs fine once gated. `lua_builder.strip_lua_comments()`
    is kept anyway (blanking, not deleting, so `GetCurrentLine` line numbers
    still match the repo file).
- **`LuaDelete(self, fileName)`** — `Robot.py:9600`. Same undecorated-wrapper
  pattern (delegates to `__FileDelete`). Bare int.
- **`GetLuaList(self)`** — `Robot.py:9616`. Returns
  `(0, lua_num, luaNames_list)` on success or `(err, None, None)` on failure —
  a **3-tuple**, not the usual 2-tuple.
