# HMI abilities: layout, reading, doing, tunnels, getting in

`$H` is the SSH prefix from SKILL.md:
`ssh -i $HOME/.ssh/id_ed25519_weldflex -o BatchMode=yes -o ConnectTimeout=8 weldflex@192.168.1.132`.
Checked against the HMI on 2026-10-05 unless a line says otherwise.

## What's where

| Path on the HMI | What it is | In git? |
|---|---|---|
| `~/WeldFlex` | Clone of `https://github.com/gagesmith3/WeldFlex.git` (public HTTPS fetch, `main` only) | n/a |
| `~/WeldFlex/.env` | Live config: `WELDFLEX_ROBOT_IP=192.168.57.2`, CNDE/8083 ports, `WELDFLEX_DSC_RATE_100_PCT_MMS=1000`, `WELDFLEX_KIOSK=1` | Ignored. Backups are `.env.bak-*` |
| `backend/recipes.json` | The live part library | **Tracked**, always modified. Backups are `recipes.json.bak-<YYYYMMDD-HHMMSS>-<reason>` |
| `backend/run_history.jsonl` | One line per run | Ignored |
| `backend/run_events.jsonl` | One line per run event (stop commands, faults, phases) | Ignored |
| `backend/weld_tuning.json` | Admin → Weld Tuning (search speed, press gain), read at each Run | Ignored |
| `backend/jog_settings.json` | Jog page settings | Ignored |
| `~/WeldFlex/venv` | The Python 3.11 venv the service runs | Ignored |
| `/etc/systemd/system/weldflex-backend.service` | Installed copy of `deploy/rpi/weldflex-backend.service`, with the user and paths substituted | n/a |
| `~/deploy-backups/`, `~/weldflex-backups/`, `~/pointmoves-pre-f8853f1.tar` | Ad-hoc backups from 2026-09-23 to 09-25 | n/a |
| `~/kiosk-install.log`, `~/ed-install.log`, `~/clone.log` | Bring-up logs | n/a |

The processes:

- **Backend:** `weldflex-backend.service` runs `venv/bin/python app.py` from
  `backend/`, under waitress on :5000. It has `Restart=always`, so a crash at
  import loops every 3 s.
- **Kiosk:** getty autologin on tty1 → `.bash_profile` →
  `deploy/rpi/kiosk-session-cage.sh` → `cage` → `chromium --kiosk
  http://localhost:5000/operator`. The script waits for the backend, then
  relaunches cage whenever it exits.
- **nginx:** 127.0.0.1:8081 and :9999 proxy the controller's own web app for
  Admin → Robot Web App.

## Reading

- **Runs:** use `$H 'tail -n 3 ~/WeldFlex/backend/run_history.jsonl'` for the
  latest runs, and `run_events.jsonl` for what happened inside them. The app
  serves the same data as JSON from `/api/reports/summary`,
  `/api/reports/parts/<part_id>` and `/api/reports/runs/<run_id>`.
- **Recipes:** to diff them, copy them back with
  `scp -i ~/.ssh/id_ed25519_weldflex weldflex@192.168.1.132:WeldFlex/backend/recipes.json "<scratchpad>/"`.
- **Backend log:** `journalctl -u weldflex-backend`. The SDK's prints are in
  Chinese, and `PYTHONUNBUFFERED=1` makes them arrive live.
- **Kiosk log:** `journalctl -t weldflex-kiosk` shows cage restarts,
  `wlr-randr` errors and "still waiting for backend".
- **Requests from the kiosk browser:** Flask/waitress request lines are *not*
  in the journal. To see what the kiosk browser requests, run
  `sudo -n tcpdump -i lo -A port 5000`. That is how the Home long-press bug was
  found.
- **Backend uptime:** `systemctl show weldflex-backend -p ActiveEnterTimestamp --value`.
- **Robot link:** in `ip -4 -br a`, `eth0` UP at 192.168.57.100 means the cable
  is in and the robot's box is up. The app's own view of the link is the
  connection chip on the panel. Don't open sockets to the controller yourself.
- **Wi-Fi:** `nmcli -t -f NAME,DEVICE,TYPE con show`. The profiles are
  `iwt-network` ("IWT Network", priority 10, preferred), `preconfigured`
  ("IWT Network_IoT", priority 5), `weldflex-hotspot`, and `robot-eth0`.

## Doing (each needs the user's OK)

| Action | Command | Effect |
|---|---|---|
| Restart the backend | `sudo -n systemctl restart weldflex-backend` | Back in a few seconds. Never mid-run (SKILL.md pitfall 3). |
| Reload the kiosk browser | `pkill -x cage` | The session loop relaunches it in about 2 s, and the screen blanks briefly. |
| Rerun the installer | `cd ~/WeldFlex && sudo -n bash deploy/rpi/install_rpi_kiosk.sh` | Idempotent. Runs apt, reinstalls the unit, nginx site, polkit rule and dispatcher. Takes minutes. |
| Reboot | `sudo -n reboot` | About a minute. The SSH session drops. |
| Test the Wi-Fi polkit rule | `sudo -n systemd-run --uid=weldflex --wait --pipe nmcli general permissions` | Read-only. A plain SSH shell always shows "yes", because it's an active session. |

## The controller's web app from a laptop

The robot's web app is only reachable on the robot subnet, so tunnel through
the HMI. Both local ports must be exactly these, because the app hard-codes its
websocket port:

```bash
ssh -i ~/.ssh/id_ed25519_weldflex -N -L 8081:127.0.0.1:8081 -L 9999:127.0.0.1:9999 weldflex@192.168.1.132
```

Then browse to `http://localhost:8081`. This is the same line as the README's
"Run WebApp". The controller only serves it on the network card picked in its
own Network Settings. Until WebApp is moved to card 0 (the user port), it
returns 502.

## When SSH fails

1. **Ping 192.168.1.132.** It dropped off on the morning of 2026-10-05 and came
   back later, so retry once before diagnosing.
   - If it never answers, the panel may have a new DHCP lease. Its wlan0 MAC is
     `2c:cf:67:a0:49:9a`, so the owner can look it up in the router's DHCP
     table.
   - It may be in hotspot mode. Join its hotspot and use `10.42.0.1`.
2. **It pings, but SSH refuses or asks for a password.** Check that `-i` points
   at the weldflex key; `BatchMode` turns a password prompt into an immediate
   failure. A host-key-changed error means the panel was reflashed. Ask before
   running `ssh-keygen -R`.
3. **Raspberry Pi Connect** (rpi-connect-lite, remote shell only) is signed in
   on the HMI. The owner can open a shell from connect.raspberrypi.com when SSH
   can't reach the panel. Claude can't use it, so hand the user the commands.
4. **Address history:** `192.168.1.131` was eth0 on the office router during
   the 2026-09-22 bring-up. eth0 has been the robot link since 2026-09-23. The
   old Pi 4 kiosk was `iwt@weldflex.local`, and is no longer in use.
