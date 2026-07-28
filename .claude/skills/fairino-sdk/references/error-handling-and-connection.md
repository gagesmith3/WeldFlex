# Safety, error handling & connection state

## The `xmlrpc_timeout` decorator — `Robot.py:536`

```python
def xmlrpc_timeout(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if RPC.is_connect == False:
            return -4
        else:
            result = func(self, *args, **kwargs)
            return result
    return wrapper
```

This is the connection-error convention's enforcement point: any decorated
method returns bare `-4` immediately, with no RPC attempt, whenever
`RPC.is_connect` is `False`.

**Not universal.** Several methods have this decorator commented out
(`GetDI` and a few motion-status getters, ~lines 3324/3403/4329/4360) or
omitted entirely (`GetSafetyCode`, `LuaUpload`, `LuaDelete`). Don't assume
every SDK call will short-circuit to `-4` when disconnected — some will
instead hang, error differently, or silently serve stale local-cache data.

`RPC.is_connect` is set in `RPC.__init__` (`Robot.py:2238-2308`) based on a
successful `GetControllerIP()` XML-RPC probe (CNDE/port-20005 connectivity is
optional and does *not* block `is_connect=True`), and re-set by `reconnect()`
(`Robot.py:2370`).

## Error code convention — `RobotError` class, `Robot.py:548`

| Code | Constant | Meaning |
|---|---|---|
| `0` | `ERR_SUCCESS` | Success |
| `-1` | `ERR_OTHER` | Other/unspecified error |
| `-2` | `ERR_SOCKET_COM_FAILED` | Socket communication failure |
| `-3` | `ERR_XMLRPC_COM_FAILED` | XML-RPC communication failure |
| `-4` | `ERR_RPC_ERROR` | Disconnected — returned directly by `xmlrpc_timeout` when `RPC.is_connect` is `False` |

`backend/robot_service.py`'s `_has_conn_error()` treats any of `{-4,-3,-2}`
appearing anywhere in a nested return value as fatal, and forces client
recreation on the next call — the validated, working convention to replicate
for any new call path.

## `GetSafetyCode(self)` — `Robot.py:2762`

**No decorators at all** — no `@log_call`, no `@xmlrpc_timeout`, no
`reconnect_flag` wait. Pure local read, runs unconditionally even while
disconnected:
```python
return 99 if (safety_stop0_state==1 or safety_stop1_state==1) else 0
```
`99` is a distinct "safety stop asserted" sentinel — **not** one of the
`RobotError` connection-failure codes above, and not itself a comm error.
Called internally as a pre-flight gate by `StartJOG`, `ProgramRun`, and
`ProgramResume` — each returns `99` in place of actually running if a safety
stop is latched (see `motion-and-jog.md` and
`program-and-file-management.md`).

## `ResetAllError(self)` — `Robot.py:5298`

No params, real RPC call, bare int. Docstring: only clears **resettable**
errors — some fault states require a physical reset, not just this call.

## `GetRobotErrorCode(self)` — `Robot.py:6143`

**This is the correct/only name.** `GetRobotErrCode` (a plausible-looking
alternate spelling) **does not exist anywhere in `Robot.py`** — calling it
raises `AttributeError`. (`backend/robot_service.py`'s `diagnostics()` already
guards this in a try/except, treating the failure as "unavailable" — don't
introduce a second, unguarded call to the wrong name elsewhere.)

Real method is a **local-cache read**, always error `0` (RPC call commented
out just above it):
```python
return 0, [self.robot_state_pkg.main_code, self.robot_state_pkg.sub_code]
```
Returns `(0, [maincode, subcode])`.
