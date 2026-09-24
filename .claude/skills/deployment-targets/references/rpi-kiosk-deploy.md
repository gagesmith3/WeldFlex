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
| Packages | `cage seatd wlr-randr` | `xserver-xorg xinit matchbox-window-manager xinput x11-xserver-utils unclutter` |
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
2. **Packages**: `chromium curl git nginx python3 python3-pip python3-venv` plus
   the stack-specific set above. The Bookworm-and-later binary is `chromium`,
   **not** `chromium-browser` — the old Raspbian name fails with "not found".
   - **2b (cage only)**: `dpkg-divert`s Adwaita's `default` cursor out of the
     way — the only thing that removes cage's stuck centre cursor (see the
     installer comments for what didn't work).
   - **2c (cage only)**: udev rule `99-weldflex-touch-rotate.rules` sets a
     libinput calibration matrix on the Goodix touchscreen, because wlroots 0.18
     does not rotate touch with the output. The matrix pairs with the session
     script's `KIOSK_ROTATE`; change both together.
3. **Python venv** at `$PROJECT_DIR/venv`, `pip install -r requirements.txt`
   (Flask + python-dotenv + waitress — the FAIRINO SDK is stdlib-only and is
   added to `sys.path` at import time, never pip-installed).
4. **`.env` check**: warns only; it does not copy `.env.rpi.example` for you.
   - **4b. Robot web app proxy**: installs `nginx-robot-web.conf` as an nginx
     site with `ROBOT_IP` taken from `.env`'s `WELDFLEX_ROBOT_IP` (default
     `192.168.58.2`), removes Debian's default site, and reloads nginx.
     Loopback only: `:8081` proxies the controller's web app for Admin → Robot
     Web App, and `:9999` proxies its websocket. The conf's comments explain
     the header, cookie and websocket rewrites.
   - **4c. Wi-Fi polkit rule**: installs `10-weldflex-wifi.rules` to
     `/etc/polkit-1/rules.d/` with `KIOSK_USER` substituted. It grants the kiosk
     user four NetworkManager actions (network-control, settings.modify.system,
     wifi.scan, enable-disable-wifi) so Settings → Wi-Fi works without root.
     **The `10` prefix matters.** Debian's `49-polkit-pkla-compat.rules` runs the
     vendor `.pkla`, which answers "no" to `settings.modify.system` for sudo/netdev
     users outside an active session, and the first rule with an answer wins. A
     `50-` rule never ran for that action. Check it with
     `sudo systemd-run --uid=<kiosk user> --wait --pipe nmcli general permissions`.
     A normal SSH shell is an active session, so it shows "yes" regardless.
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
3. `while true; do cage -- bash "$0" --in-cage; sleep 2; done`. cage exits when
   its child exits, so this one loop covers both a Chromium crash and a
   compositor crash. The X11 script only ever supervised Chromium.
4. The `--in-cage` second stage runs inside the compositor: `wlr-randr` rotates
   (`KIOSK_ROTATE`, default 90) and scales (`KIOSK_SCALE`, default 1.6) the
   `KIOSK_OUTPUT` (default `DSI-2`), then `exec`s Chromium. Chromium's
   `--force-device-scale-factor` is not used because under Wayland it rendered
   into a corner of the panel.

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
    ipv4.method manual ipv4.addresses 192.168.58.100/24 \
    ipv4.never-default yes ipv6.method disabled
sudo nmcli con up robot-net
```

`ipv4.never-default yes` keeps the robot link from ever owning the default
route, so internet (updates, apt) stays on Wi-Fi. The first ED-HMI3020
bring-up (2026-09-22) lost internet after WeldFlex was set up on it; a
route-stealing static eth0 is the suspected cause (not confirmed — the unit was
reflashed instead of diagnosed).

Verify with `ip a` — `eth0` must show `state UP`, not `NO-CARRIER` (ethernet must
be physically plugged in before the connection comes up).

## Wi-Fi from the kiosk (Settings → Wi-Fi)

`backend/wifi.py` changes Wi-Fi without touching the robot link:

- Every nmcli call names `wlan0` (`WELDFLEX_WIFI_IFACE` overrides it). Deletes only
  ever hit `802-11-wireless` profiles, checked again just before the delete.
- Before connecting, it records the interface that routes to the robot and that
  interface's subnet. A new network is rolled back if its subnet overlaps the robot's
  (for example, a shop Wi-Fi on 192.168.57.0/24) or if the robot route moves.
  Rollback deletes the new profile and brings the previous Wi-Fi profile back up.
  A failed connect (such as a wrong password) rolls back the same way.
- A saved network reconnects without a password. To change its password,
  forget it first. This avoids NetworkManager's version-dependent handling of
  `device wifi connect` against an existing profile.
- WPA-Enterprise (802.1X) networks are listed but cannot be joined from the card.
  Set them up over SSH.
- polkit cannot restrict `settings.modify.system` to Wi-Fi profiles, so the
  eth0 guard lives in `wifi.py`, not in the rule.
- The password goes on nmcli's command line, so it is briefly visible in `ps` to
  local users. It is never logged.

**Not yet run on hardware.** Only the fake-nmcli tests in `tests/test_wifi.py`
have run. Verify the polkit rule and one real join and rollback on the Pi.

## `.env` on the RPi

Copy `deploy/rpi/.env.rpi.example` → `.env` at the project root:

```bash
WELDFLEX_ROBOT_IP=192.168.58.2
WELDFLEX_CNDE_PORT=20004        # code default is 20005, which does not work here
WELDFLEX_CNDE_PERIOD_MS=20
WELDFLEX_STATUS_PORT=8083       # pushed status feed; period is set on the pendant
WELDFLEX_FEED_STALE_S=3.0
PORT=5000
WELDFLEX_KIOSK=1
```

**Set `WELDFLEX_CNDE_PORT` explicitly.** Leaving it unset is not neutral — it
falls back to `20005`, the port that times out on this firmware. This has
already bitten the live dev `.env`. It no longer kills force telemetry outright
(the port-8083 push is the primary force source and CNDE is only the fallback
beneath it), but it does remove the fallback and leave a receiver retrying a
port that will never answer — set it.

Deliberately omits `WELDFLEX_FAIRINO_PATH` so `_bootstrap_sdk()`'s
`sys.platform` auto-detect resolves to `fairino-python-sdk-main/linux/fairino`.
Also omits `WELDFLEX_PROGRAM_PATH`/`WELDFLEX_STUDS_DATA_PATH`/`WELDFLEX_FTP_USER`/
`WELDFLEX_FTP_PASS` — those fall back to `app.py`'s defaults (SKILL.md gotchas
#5–6).

## Cross-reference

Top-level SKILL.md's "CNDE connect-gate" section and gotcha table.
`../../sdk-alignment-findings.md` for the dated discovery record.
