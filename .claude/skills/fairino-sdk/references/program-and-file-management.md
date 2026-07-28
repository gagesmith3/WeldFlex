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

- **`GetProgramState(self)`** — `Robot.py:7164`. **Local-cache read, not RPC**
  (`return 0, self.robot_state_pkg.robot_state`; the real RPC call is
  commented out just above it). Always error `0`. Docstring documents values
  `1`=stopped/no program, `2`=running, `3`=paused — **but** the underlying
  struct field comment (`Robot.py:~194`) documents a 4th value, `4`=drag(teach)
  mode, that `GetProgramState`'s own docstring never mentions. If you poll
  this while the robot is in drag-teach mode, expect `4`, not one of the
  documented three.
- **`GetCurrentLine(self)`** — `Robot.py:7050`. Real RPC call. Returns
  `(0, line_num)` / `(err, None)`. Used for line-based progress tracking (see
  the `weldflex-app` skill's liberty-test completion-detection pattern).

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
    `(tmp_error, _error[1])` — the reverse of the SDK's usual
    "tuple-on-success, bare-int-on-failure" pattern seen elsewhere.
- **`LuaDelete(self, fileName)`** — `Robot.py:9600`. Same undecorated-wrapper
  pattern (delegates to `__FileDelete`). Bare int.
- **`GetLuaList(self)`** — `Robot.py:9616`. Returns
  `(0, lua_num, luaNames_list)` on success or `(err, None, None)` on failure —
  a **3-tuple**, not the usual 2-tuple.
