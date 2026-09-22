# Windows dev machine

Development target: Gage's Windows 11 desktop. No systemd, no kiosk browser,
no touchscreen — this is where features get written and smoke-tested before
the RPi ever sees them.

## Running it

```
python backend\app.py
```

(per the root `README.md`). No install script, no venv-activation wrapper —
just the checked-in `venv\Scripts\python.exe` or system Python with
`requirements.txt` installed. `app.py`'s `if __name__ == "__main__":` block
runs production `waitress.serve(...)` on `PORT` (env `PORT`, default `5000`)
by default; `app.run(host="0.0.0.0", port=PORT, debug=False,
use_reloader=False)` only runs instead if `WELDFLEX_DEV_SERVER=1` is set.
`use_reloader=False` applies either way and is not platform-specific — just
this app's standing choice (avoids the reloader spawning a second process
that double-opens the robot connection).

## `.env` on this machine

Root `.env` (not `.env.example` — the real file, gitignored) hardcodes:

```
WELDFLEX_FAIRINO_PATH=C:/Users/Gage/Desktop/WeldFlex/fairino-python-sdk-main/windows
```

This means `_bootstrap_sdk()`'s `sys.platform`-based auto-detect branch
(`robot_link.py` — this and `_bootstrap_sdk()` moved out of `robot_service.py`
when the connection layer was split; see the top-level SKILL.md) never
actually executes on this machine — the env
override in the candidates list always wins first. If you need to test the
auto-detect path itself (e.g. verifying it'd correctly resolve `windows/` on
a machine without the override), temporarily unset the var rather than
trusting that it's been exercised locally.

`WELDFLEX_KIOSK` is unset here → `KIOSK_MODE = False` → templates render with
full desktop chrome, not the touch-target CSS breakpoints. **The kiosk touch
UI cannot be visually verified on this machine** without either setting
`WELDFLEX_KIOSK=1` locally or testing on the actual RPi hardware — don't
assume a change "looks fine" on Windows dev implies it's fine at 800×480
touch scale.

## `/operator/settings` has no OS-integration routes to test here

The NTP/timezone/wifi-scan/wifi-connect/reboot/shutdown routes this section
used to describe were removed in `d446c3a` ("Refactor settings page and remove
unused components", 2026-07-28). `/operator/settings` now renders
`settings.html`, a static "Settings are coming soon" page — there's nothing
platform-gated to test on either machine. If that functionality is rebuilt,
document its actual shape here rather than assuming it returns to the old
`sys.platform != "win32":`-wrapped-subprocess pattern.

## The Chinese-debug-print / UTF-8 fix

See the top-level SKILL.md — `robot_service.py` force-reconfigures stdio to
UTF-8 before importing the SDK specifically because Windows' default console
codec can't encode the SDK's Chinese debug `print()`s. This is the one
Windows-specific workaround that lives in the app itself rather than being
gated behind `sys.platform` — worth knowing about if a future SDK vendor
update ever seems to cause an unexplained crash on Windows only; check for a
`UnicodeEncodeError` under the reported failure first.

## Cross-reference

Top-level SKILL.md's target-comparison table and gotcha list; `fairino-sdk`
skill for what actually differs (nothing but the folder) between the
`windows/` and `linux/` vendored SDK copies.
