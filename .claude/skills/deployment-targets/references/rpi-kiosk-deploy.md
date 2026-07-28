# RPi kiosk deploy — install pipeline, session stacks, networking

Production target: Raspberry Pi OS **Lite**, full-screen touchscreen kiosk
(800×480 — see the `weldflex-app` skill's touch/CSS notes), no keyboard/mouse in
normal operation. Everything under `deploy/rpi/` implements this.

Lite is the intended base: it ships no compositor, no display manager, and no
desktop, so the installer builds the entire session stack itself rather than
fighting a preinstalled one. It still runs on the Desktop image, where the
display-manager teardown steps stop being no-ops.

## Two session stacks

As of the Lite rebuild there are two, selected by an installer flag:

| | cage (Wayland) — **default** | X11 — fallback |
|---|---|---|
| Install | `sudo bash deploy/rpi/install_rpi_kiosk.sh` | `… install_rpi_kiosk.sh --x11` |
| Session script | `kiosk-session-cage.sh` | `kiosk-session-x11.sh` |
| Packages | `cage seatd` | `xserver-xorg xinit matchbox-window-manager xinput x11-xserver-utils unclutter` |
| Launched by | `.bash_profile` → script directly | `.bash_profile` → `startx` → script |
| Supervises | compositor **and** browser | browser only |

**Why cage is the default.** Four steps in the X11 script are pure X11-isms that
cage removes outright rather than works around:

- `matchbox-window-manager` — cage owns fullscreen, so the "WM must start before
  Chromium or fullscreen positioning is wrong" ordering trap disappears.
- `xinput float <touch-id>` — this existed **only** because X11 funnels touch
  through the master pointer, so touching the screen dragged a cursor. Wayland
  carries touch and pointer as separate protocols; the bug class is gone, not
  patched. (`unclutter` and `xsetroot` were never sufficient for this.)
- `unclutter` — no pointer device means no cursor to hide.
- `xset s off` / `-dpms` — no X server to blank.

**Why X11 is kept.** It is the configuration proven working on this exact
hardware, ft5x06 touchscreen included. cage's behavior with that panel is the
main thing to validate on the target; falling back is one installer re-run.

**Unverified on hardware** (check before trusting): cage's cursor and
idle/blanking defaults, and whether Chromium needs more than
`--ozone-platform=wayland` on the installed Chromium version. Only bare
`cage -- CMD` is relied on in the script — cage's option set is small and
version-dependent, so confirm anything else against `cage --help` on the target.

## Install pipeline (`install_rpi_kiosk.sh`)

Every step is idempotent — re-run the installer to iterate rather than
hand-editing installed copies.

1. **Display server.** Only the `--x11` path calls
   `raspi-config nonint do_wayland W1`, and tolerates its absence: on Lite there
   is no desktop session to switch away from, so the toggle may not exist. The
   cage path skips it entirely.
2. **Packages**: `chromium curl python3 python3-pip python3-venv` plus the
   stack-specific set above. The Bookworm-and-later binary is `chromium`, **not**
   `chromium-browser` — the old Raspbian name fails with "not found".
3. **Python venv** at `$PROJECT_DIR/venv`, `pip install -r requirements.txt`
   (Flask + python-dotenv + waitress — the FAIRINO SDK is stdlib-only and is
   added to `sys.path` at import time, never pip-installed).
4. **`.env` check**: warns only; it does not copy `.env.rpi.example` for you.
5. **Session scripts**: `chmod +x` on both, and `usermod -aG video,input,render`
   for the kiosk user. logind normally grants wlroots its DRM/input access via
   the seat; the group membership is belt-and-braces and harmless on X11.
6. **systemd unit**: `sed`-substitutes `/home/pi/WeldFlex` → `$PROJECT_DIR` and
   `User=pi` → the install user into `weldflex-backend.service`, installs to
   `/etc/systemd/system/`, enables it.
7. **Autologin**: disables `lightdm`/`gdm3` (no-ops on Lite), writes a
   `getty@tty1.service.d/autologin.conf` override, and writes the kiosk user's
   `.bash_profile` to exec the stack's session script when `XDG_VTNR == 1` and
   no display is up yet.

**Why getty+startx instead of LightDM.** A LightDM custom-session autologin was
tried first and abandoned: PAM group checks, the Wayland greeter appearing
despite X11 being forced, and session-type auto-detection — all failure modes
with no clean fix. Don't reintroduce a display manager without solving all three.

**Why not a systemd user unit for the session.** It would supervise better and
allow `systemctl --user restart` instead of a reboot, but `systemd --user` units
run in `user@.service`, *outside* the login session scope — so seat0 access for
DRM/input is not guaranteed. getty autologin gives a real logind session on
seat0, which is what the compositor needs. The supervising loop therefore lives
inside the session script instead. Revisit only with hardware proof.

## Session script runtime sequence (cage)

1. Redirect stdout/stderr through `logger -t weldflex-kiosk` so the session lands
   in journald. Only stdout/stderr are touched — cage keeps the VT and opens its
   own devices. The pre-Lite session logged nowhere at all.
2. Poll `curl -sf http://localhost:5000/` until the backend answers. systemd
   starts the backend and the session independently with no ordering; an `After=`
   would not help, since it orders start, not socket readiness.
3. `while true; do cage -- chromium --kiosk …; sleep 2; done`. cage exits when its
   child exits, so this one loop covers both a Chromium crash and a compositor
   crash. The X11 script only ever supervised Chromium.

Chromium flags and why: `--ozone-platform=wayland` (cage path only),
`--no-sandbox` (carried over from the X11 stack; worth testing removal, since a
normal user session should have working namespace sandboxing),
`--disable-dev-shm-usage` (RPi `/dev/shm` defaults to 64MB — Chromium crashes
without it), `--user-data-dir=/tmp/weldflex-kiosk` (isolated profile, cleared on
reboot since `/tmp` is normally tmpfs).

## Watching it

```bash
journalctl -u weldflex-backend -f    # Flask + SDK
journalctl -t weldflex-kiosk -f      # compositor + Chromium
```

## Line endings — `.gitattributes`

The repo is authored on Windows with `core.autocrlf=true`. Without the
root `.gitattributes` pinning `*.sh`, `*.service`, `deploy/**` and `*.lua` to
`eol=lf`, a Windows checkout rewrites these with CRLF and they fail on the Pi
with `bad interpreter: /bin/bash^M`. This bites on any transfer route other than
a fresh `git clone` on the Pi — scp, rsync, a mounted share, a USB copy. Don't
remove those rules.

## `weldflex-backend.service`

Runs `venv/bin/python app.py`, which serves under **waitress**, not the Werkzeug
dev server (bounded thread pool + client timeouts; the dev server grows threads
without limit when the robot is unreachable and pages keep polling). Set
`WELDFLEX_DEV_SERVER=1` to force the dev server.

`Wants=`/`After=network-online.target` — plain `network.target` does not wait for
an address, so the first connect attempt on a cold boot always failed.
`Restart=always`/`RestartSec=3`, `TimeoutStopSec=10`, `KillMode=mixed`, and
`PYTHONUNBUFFERED=1` so the SDK's prints reach journald live.

The old `ExecStartPre` `sed` that re-patched the SDK connect gate on every boot
has been **removed** — that fix lives in the vendored source now (see the
top-level SKILL.md's "CNDE connect-gate"). `deploy/rpi/weldflex-kiosk.desktop`
has also been deleted; it was never referenced by the installer or the autologin
flow.

## Networking — robot subnet

The robot lives on `192.168.58.0/24` (controller at `.2`). `eth0` needs a static
IP on that subnet:

```bash
sudo nmcli con add type ethernet ifname eth0 con-name robot-net \
    ipv4.method manual ipv4.addresses 192.168.58.100/24
sudo nmcli con up robot-net
```

Verify with `ip a` — `eth0` must show `state UP`, not `NO-CARRIER` (ethernet must
be physically plugged in before the connection comes up).

## `.env` on the RPi

Copy `deploy/rpi/.env.rpi.example` → `.env` at the project root:

```bash
WELDFLEX_ROBOT_IP=192.168.58.2
PORT=5000
WELDFLEX_KIOSK=1
```

Deliberately omits `WELDFLEX_FAIRINO_PATH` so `_bootstrap_sdk()`'s
`sys.platform` auto-detect resolves to `fairino-python-sdk-main/linux/fairino`.
Also omits `WELDFLEX_PROGRAM_PATH`/`WELDFLEX_STUDS_DATA_PATH`/`WELDFLEX_FTP_USER`/
`WELDFLEX_FTP_PASS` — those fall back to `app.py`'s defaults (SKILL.md gotchas
#5–6).

## Cross-reference

Top-level SKILL.md's "CNDE connect-gate" section and gotcha table.
`../../sdk-alignment-findings.md` for the dated discovery record.
