---
name: deployment-targets
description: Conventions for WeldFlex's two deploy targets — the Windows dev machine and the Raspberry Pi OS Lite production kiosk. Use when writing or reviewing any sys.platform branch, editing anything under deploy/rpi/, touching the SDK path bootstrap (_bootstrap_sdk in robot_link.py), changing .env/.env.example/.env.rpi.example, or working on the /operator/settings OS-integration routes (NTP, timezone, wifi, reboot, shutdown). Covers the sys.platform != "win32" guard convention, the windows/linux SDK folder auto-detect vs. WELDFLEX_FAIRINO_PATH override, the systemd + cage/Wayland kiosk install pipeline (with its X11 fallback stack), the .gitattributes CRLF guard, and the CNDE-port connect-gate patch history.
---

# WeldFlex deploy targets — Windows dev vs. RPi kiosk

WeldFlex runs identically in source (`backend/app.py`, `backend/robot_service.py`)
on two very different machines: a Windows desktop used for development, and a
Raspberry Pi (RPi OS Lite) running full-screen as the shop-floor kiosk. The
app code branches on `sys.platform`/`os.getenv` rather than shipping two
codebases. This skill covers what differs and why; for the FAIRINO SDK itself
see the `fairino-sdk` skill, for Flask/route conventions see `weldflex-app`.

## The two targets

| | Windows dev | RPi kiosk (production) |
|---|---|---|
| OS | Windows 11 | Raspberry Pi OS **Lite** |
| User | Gage, desktop | `pi` (or install-time `$SUDO_USER`), `/home/pi/WeldFlex` |
| Entry point | `python backend\app.py` (manual, Werkzeug) | `weldflex-backend.service` (systemd, auto-start, waitress) |
| Browser | none — hit Flask directly or via a normal browser tab | Chromium `--kiosk` under **cage/Wayland** (X11 fallback available), auto-launched via `getty` autologin |
| SDK dir | `fairino-python-sdk-main/windows/fairino` | `fairino-python-sdk-main/linux/fairino` |
| `.env` source | root `.env` (hardcodes `WELDFLEX_FAIRINO_PATH`) | copied from `deploy/rpi/.env.rpi.example` (omits it — auto-detect) |
| `WELDFLEX_KIOSK` | unset (full desktop chrome) | `1` (touch-target CSS breakpoints active) |

## Top gotchas

| # | Gotcha | Detail |
|---|---|---|
| 1 | OS-integration settings routes silently no-op on Windows | `timedatectl`/`nmcli`/`iwgetid`/`reboot`/`shutdown` calls in `app.py` are all wrapped `if sys.platform != "win32":` — on Windows they skip the subprocess call entirely and report `ok=True` with an empty payload, not an error. Testing `/operator/settings` locally will look like every button "does nothing" — that's by design, not a bug to fix. |
| 2 | SDK path auto-detect is untested on the dev box itself | `_bootstrap_sdk()` picks `windows/`-vs-`linux/` from `sys.platform`, but `WELDFLEX_FAIRINO_PATH` always wins if set — and the dev-machine `.env` hardcodes it to an absolute Windows path. The `sys.platform` branch only actually gets exercised via `deploy/rpi/.env.rpi.example`, which deliberately omits the var. |
| 3 | The RPi systemd unit's SDK patch step is **gone** | `weldflex-backend.service` used to `sed`-patch `if cnde_ok and xmlrpc_ok:` → `if xmlrpc_ok:` in the vendored `Robot.py` via `ExecStartPre` at every service start. Commit `452bbfc` ("fixit8", 2026-07-15) baked that edit into both vendored copies, making the `sed` a no-op; the line has since been deleted from the unit. See "CNDE connect-gate" below. |
| 4 | `libfairino/` and `fairino/build/lib.*` are unused | Both platform dirs also ship a compiled Cython extension (`libfairino/Robot.*.pyd`/`.so`) and stray build artifacts inside `fairino/build/`. `_bootstrap_sdk()` only ever imports the plain `fairino/Robot.py` source — never add `libfairino` to a deploy step. |
| 5 | `WELDFLEX_FTP_USER`/`WELDFLEX_FTP_PASS` are set but unused | Present in root `.env`/`.env.example`; no `.py` file references them (`LuaUpload` goes over XML-RPC, not raw FTP). Vestigial — don't assume Lua upload depends on them. |
| 6 | RPi `.env` doesn't set `WELDFLEX_PROGRAM_PATH`/`WELDFLEX_STUDS_DATA_PATH` | `deploy/rpi/.env.rpi.example` only sets `WELDFLEX_ROBOT_IP`/`PORT`/`WELDFLEX_KIOSK` — those two paths fall back to `app.py`'s hardcoded defaults on the RPi, same as everywhere else. Doesn't change the `WeldFlex.lua` program-name landmine (`../../sdk-alignment-findings.md`), just confirms it isn't papered over by RPi-specific config. |

## Windows-only stdout fix (applies everywhere `robot_link.py` runs)

The top of `robot_link.py` force-reconfigures `sys.stdout`/`sys.stderr` to
UTF-8 unconditionally, before importing the SDK. (This and `_bootstrap_sdk()`
both moved out of `robot_service.py` when the connection layer was split into
`robot_link.py` — older notes pointing at `robot_service.py` are stale.)

```python
# robot_link.py:47-49
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8", errors="replace")
```

The SDK prints Chinese debug text on every RPC call. Windows' default console
codec (cp1252) can't encode it, raising `UnicodeEncodeError` — which surfaces
as what looks like a connection failure, not an encoding bug. This is a no-op
on Linux (already UTF-8 by default) so it's safe to leave unconditional rather
than platform-gated.

## CNDE connect-gate — patch history

The FR-16 firmware only speaks CNDE on port 20004; this SDK's CNDE client
targets port 20005, so CNDE always fails against real hardware. The original
vendored code only set `RPC.is_connect = True` when **both** CNDE and XML-RPC
succeeded (`if cnde_ok and xmlrpc_ok:`), which made every real robot connection
report `-4` even though XML-RPC (the only channel this app actually uses)
worked fine. That condition was changed to `if xmlrpc_ok:` directly in both
vendored `Robot.py` copies in commit `452bbfc` ("fixit8", 2026-07-15) — the fix
is baked into the SDK source now, not applied at deploy time. `error-handling-and-connection.md`
in the `fairino-sdk` skill documents the current (fixed) behavior as fact. The
now-redundant `ExecStartPre` `sed` has since been deleted from
`weldflex-backend.service`, so nothing re-patches the SDK at boot any more. **If
the SDK is ever re-vendored from a fresh upstream drop, re-check this line** —
there is no longer a deploy-time safety net that would silently fix it.

## Reference files

| File | Load this when... |
|---|---|
| `references/rpi-kiosk-deploy.md` | Installing, debugging, or modifying the RPi kiosk (systemd unit, X11/Chromium session, autologin, networking) |
| `references/windows-dev.md` | Working on the Windows dev machine, or explaining why an OS-integration feature "does nothing" locally |

Audit log: `../../sdk-alignment-findings.md`.
