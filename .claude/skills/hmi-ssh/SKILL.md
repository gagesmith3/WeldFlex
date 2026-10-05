---
name: hmi-ssh
description: Getting onto WeldFlex's production HMI (the EDATEC ED-HMI3020 Pi 5 kiosk, weldflex@192.168.1.132) over SSH, and pushing local updates to it safely. Use whenever a task involves SSH or scp to the HMI, Pi, panel or kiosk; deploying, pushing, updating or rolling back code "on the HMI"; restarting the backend or the kiosk browser; checking which commit the HMI runs; or reading its logs, run history, recipes, .env or screen. Covers access (key, address, fallbacks), what is safe to do there, the read-only preflight script, the two deploy routes (GitHub pull vs. git bundle), per-path post-pull steps, restart and verification, rollback, and the traps that have bitten before: the live recipes.json tracked in git, restarts that orphan a running job, Python 3.11 on the HMI, and a kiosk page that keeps old JS/CSS.
---

# The WeldFlex HMI over SSH: access, abilities, deploys

The HMI is the production operator panel at the cell. It is an EDATEC
ED-HMI3020-101C (a Raspberry Pi 5 on Bookworm Lite with the EDATEC kernel). It
runs this repo's backend under systemd and Chromium full-screen under cage. Its
checkout is a git clone of the same GitHub repo as this PC, so **deploying means
moving the HMI's `main` forward through git, then restarting the backend.**
Nothing else.

An operator may be standing at it, so treat every write there as a production
change. For how the kiosk was installed (session stack, nginx, polkit, robot
networking), see the `deployment-targets` skill. This skill covers getting on,
what you can do once you're there, and shipping updates.

## Access

| | |
|---|---|
| Command | `ssh -i ~/.ssh/id_ed25519_weldflex weldflex@192.168.1.132` |
| From the Bash tool | Add `-o BatchMode=yes -o ConnectTimeout=8`, so a dead host or a missing key fails in seconds instead of hanging on a prompt. Shell variables don't persist between Bash calls, so re-define `$H` (below) in each one. |
| Key | `~/.ssh/id_ed25519_weldflex` on this PC (`$HOME\.ssh\id_ed25519_weldflex` in PowerShell). There is no `~/.ssh/config` entry, so always pass `-i`. |
| Address | `192.168.1.132` on `wlan0`, by DHCP from the shop Wi-Fi "IWT Network". The hostname is `weldflex`. Don't use `weldflex.local`: from this PC, mDNS answers with stale or IPv6 addresses. |
| Hotspot mode | `weldflex@10.42.0.1`, from a laptop joined to the panel's own hotspot (used at trade shows). There's no internet in that mode. |
| User | `weldflex`, with passwordless sudo. Use `sudo -n`, so a missing rule fails instead of prompting. |
| Never via eth0 | `eth0` is the robot link only (`robot-eth0`, 192.168.57.100/24, controller at 192.168.57.2). It can't be reached from the office. |
| Can't connect | See `references/abilities.md` → "When SSH fails". |

## Ground rules

1. **Read freely. Write only with the user's OK for that specific change.**
   Reads are fine: git log/status, journalctl, curl of the app's own pages,
   cat, screenshots. Each of these needs a go-ahead in this conversation: a
   pull, a restart, the installer, a file edit, nmcli, a reboot. If the
   permission layer refuses an SSH write, don't route around it. Give the user
   the exact commands instead.
2. **Read the HMI's state; don't trust notes.** Memory and earlier sessions
   have been wrong about which commit the HMI runs. On 2026-10-02 another agent
   reset it to a stale `origin/main`. Run the preflight, or `git log -1` there,
   before saying what it runs.
3. **Code moves only through git.** Don't scp source files over, and don't edit
   tracked files in place. Either one leaves the checkout dirty, makes the
   `BETA <sha>` in the header lie, and turns the next pull into a merge problem.
   That was the 2026-09-25 trap, when a snapshot of uncommitted work sat on the
   HMI. `.env` and the live data files are the exceptions, and always get a
   backup first.
4. **Never talk to the robot from the HMI by hand.** The HMI sits on the
   robot's subnet, but ad-hoc SDK, XML-RPC or raw-socket probes crashed the
   controller on 2026-07-28. Never `import app` or start `app.py` by hand while
   the service runs, because that opens a second robot client. Read robot state
   through the app's own endpoints.

## Quick reference (read-only; each checked against the HMI on 2026-10-05)

```bash
H="ssh -i $HOME/.ssh/id_ed25519_weldflex -o BatchMode=yes -o ConnectTimeout=8 weldflex@192.168.1.132"

$H 'cd ~/WeldFlex && git log -1 --oneline && git status --short'          # checked out / dirty
$H 'curl -s localhost:5000/operator | grep -o "BETA [0-9a-f]*" | head -1'  # what the running backend serves
$H 'curl -s localhost:5000/ui/job/status | grep -o "every [0-9]*ms"'       # 750ms = busy, 3000ms = idle
$H 'systemctl status weldflex-backend --no-pager | head -5'
$H 'journalctl -u weldflex-backend -n 80 --no-pager -o cat'                # backend + SDK prints
$H 'journalctl -t weldflex-kiosk -n 40 --no-pager -o cat'                  # cage + Chromium
$H 'ip -4 -br a'                                                            # no eth0 line = robot off/unplugged
```

The job check reads the same panel the kiosk polls. That panel polls every
750 ms while a WeldFlex job is starting, running, paused or gated, and also
while any controller program is running or paused. Otherwise it polls every 3 s.
If the check prints nothing, the backend isn't answering. If the robot link is
down, "idle" only means the HMI isn't tracking anything.

To see the panel, take a screenshot (1280×800, already rotated), copy it back,
then Read it:

```bash
$H 'XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/hmi-shot.png'
scp -i ~/.ssh/id_ed25519_weldflex weldflex@192.168.1.132:/tmp/hmi-shot.png "<scratchpad>/hmi-shot.png"
```

`references/abilities.md` has the rest: the file layout, run history, the
controller web-app tunnel, Wi-Fi, and what to do when SSH fails.

## Pushing local updates to the HMI

The goal is the owner's: **this PC, GitHub and the HMI all on the same
`main`.** Deploy committed work only, normally `main` once it has been pushed.
Run `pytest tests` first. A bare `pytest` also collects the vendor SDK examples
and errors.

### 1. Preflight (read-only)

```bash
bash .claude/skills/hmi-ssh/scripts/hmi_preflight.sh [TARGET]    # TARGET defaults to main
```

It fetches `origin/main`, reads the HMI over SSH, and changes nothing. It
reports:

- the route (GitHub or bundle) and whether the move is a fast-forward
- the commits, and the post-pull steps the changed paths need
- the job state, and anything unexpected in the HMI's checkout
- whether the target's `backend/*.py` parses under Python 3.11, the HMI's version

Every `BLOCK` line stops the deploy until it is resolved.

### 2. Get the user's OK

Show the user the commit list, the route, the flagged steps, and whether a
restart would interrupt anything. Pushing to GitHub is a separate,
outward-facing step, so ask about that too. If the job state is busy, wait: a
restart does **not** stop the robot (pitfall 3).

### 3. Back up the live recipes, then move `main`

Always take the backup. It's cheap, and it has been needed. It must come before
the restart, because the backend migrates `recipes.json` the first time it
reads it.

```bash
$H 'cd ~/WeldFlex && cp -p backend/recipes.json backend/recipes.json.bak-$(date +%Y%m%d-%H%M%S)-pre-<short-sha>'
```

**Route A: the target is GitHub `main`.** This is the preferred route. The HMI
reaches GitHub over Wi-Fi.

```bash
$H 'cd ~/WeldFlex && git pull --ff-only -q origin main && git log -1 --oneline'
```

**Route B: the target isn't pushed.** Use this for a trial commit or a branch,
or when the HMI is on its hotspot with no internet. Bundle exactly the commits
the HMI lacks:

```bash
HMI_HEAD=$($H 'cd ~/WeldFlex && git rev-parse HEAD')
git bundle create "<scratchpad>/wf.bundle" "$HMI_HEAD"..<branch>
scp -i ~/.ssh/id_ed25519_weldflex "<scratchpad>/wf.bundle" weldflex@192.168.1.132:/tmp/wf.bundle
$H 'cd ~/WeldFlex && git bundle verify -q /tmp/wf.bundle && git pull --ff-only -q /tmp/wf.bundle <branch> && rm /tmp/wf.bundle && git log -1 --oneline'
```

After route B, the HMI's `main` is ahead of GitHub. **Don't amend, rebase or
squash those commits afterwards.** Push them exactly as they are, or the HMI
diverges and the next `--ff-only` refuses.

Both routes use `--ff-only` on purpose. A plain `git pull` on a diverged HMI
creates a merge commit there, and that commit then runs untested on the
production panel.

### 4. Do what the changed paths need, before the restart

| Changed | Do on the HMI (`cd ~/WeldFlex`) | Why |
|---|---|---|
| `requirements.txt` | `venv/bin/pip install -r requirements.txt` | A pull doesn't touch the venv. |
| `deploy/rpi/**` | `sudo -n bash deploy/rpi/install_rpi_kiosk.sh` | The unit file, nginx site, touch rule, polkit rule and dispatcher are installed *copies*, which a pull doesn't change. The installer is idempotent, but it runs apt and takes minutes. |
| `.env.example`, `deploy/rpi/.env.rpi.example` | Diff it, then ask whether the HMI's `.env` needs the new key. Run `cp -p .env .env.bak-<reason>` before any edit. | The HMI's `.env` is kept by hand (robot IP 192.168.57.2, DSC rate 1000), and a code default may not suit it. |
| `programs/*.lua` | Nothing. Tell the user to do a **Dry** run before a Live one. | The backend uploads `weld.lua` and the generated program to the controller at every Run. |
| `backend/static/**`, `backend/templates/**` | Reload the kiosk after the restart (step 5). | The page that is already open keeps its old JS/CSS. |
| Recipe format (a `_recipes_load` migration) | Keep the step 3 backup, and record its name in memory. | Rolling back past a migration needs the matching backup (Retract Z, 2026-10-01). |

### 5. Restart, reload, verify (only while idle)

```bash
$H 'sudo -n systemctl restart weldflex-backend &&
    curl -sf -o /dev/null --retry 30 --retry-delay 1 --retry-connrefused localhost:5000/ &&
    curl -s localhost:5000/operator | grep -o "BETA [0-9a-f]*" | head -1;
    journalctl -u weldflex-backend --since "-2 min" --no-pager -o cat | grep -A8 Traceback || echo "no tracebacks"'
$H 'pkill -x cage'    # only if the UI changed; the session loop relaunches cage + Chromium in ~2 s
```

Then take a screenshot. Three things must agree: the HMI's `git log -1`, the
`BETA` the backend serves, and the `BETA` on the panel. `git status --short`
there should show only `backend/recipes.json` and the backup files.

### 6. Report and record

Tell the user what moved (from → to), what was restarted, and what still needs
testing at the machine (a Dry run first if the Lua changed). Update the
branch-state memory with the commit the HMI is now on.

For a rollback, a diverged HMI, restoring recipes or `.env`, or a backend that
won't start, see `references/rollback-and-recovery.md`.

## Pitfalls

| # | Trap | Detail |
|---|---|---|
| 1 | `backend/recipes.json` is tracked in git and always modified on the HMI | It is the live part library. Never run `git stash`, `git reset --hard`, `git checkout -- .`, `git restore` or `git clean` there. Use `git reset --keep` for a rollback. If a commit touches `backend/recipes.json`, `pull --ff-only` refuses with "local changes would be overwritten". That refusal is correct: ask the user, and never discard the HMI's copy. Don't commit this PC's copy either; it's the dev library. |
| 2 | A malformed `recipes.json` reads as an empty library | `_recipes_load()` returns `[]` on a JSON error, and the next part save then writes that over the file. Before putting a hand-edited copy back, check it with `python3 -m json.tool`. |
| 3 | A restart mid-run orphans the run; it doesn't stop it | `JobManager.shutdown()` marks the run `interrupted` and exits. The controller keeps executing the uploaded program, and nothing on the HMI counts cycles. After the restart, the panel shows "Controller program running — not a WeldFlex job"; its Stop button still works. A restart also drops a loaded job that hasn't started. Check the job state every time. |
| 4 | Pulled but not restarted means new Lua with old Python | Each Run reads `programs/*.lua` from disk, but the Python that fills it in is whatever the process imported at start. Pull and restart together. The preflight warns when the served `BETA` doesn't match the checkout. |
| 5 | The HMI runs Python 3.11; this PC runs 3.13 | 3.12+ code once crashed the backend at import there (a mappingproxy dataclass default in `robot_feed.py`, 2026-09-22). The preflight's 3.11 parse catches syntax only, so read the journal after every restart. |
| 6 | The kiosk doesn't reload itself | Static files are served `Cache-Control: no-cache` with no version string, so any reload picks them up. The page that is already open keeps its old JS/CSS, and its `BETA` header, until it reloads. Use `pkill -x cage`. |
| 7 | `pkill -f` and `pgrep -f` over SSH match their own command line | Use `-x <name>` or PIDs. |
| 8 | The clone tracks `main` only | `remote.origin.fetch` is `+refs/heads/main:refs/remotes/origin/main`, so `git fetch origin feat/x` only fills `FETCH_HEAD`. Bundle branches instead (route B). |
| 9 | Wi-Fi is the only management link | A Wi-Fi change or hotspot start drops your SSH session. Never touch the `robot-eth0` profile. The signal through the aluminium case is weak, so retry a timeout before diagnosing anything. |
| 10 | The kernel is pinned | The EDATEC BSP holds kernel 6.6.31 with `apt-mark hold`, and the panel needs it. Never unhold it or leave Bookworm. OS package upgrades aren't part of a deploy. |
| 11 | The HMI's `.env` isn't this PC's | It has a different robot IP (192.168.57.2, the control box's user port), DSC rate and kiosk flag. Never copy one over the other. Edit in place, with a dated backup. |
| 12 | Other tools touch the HMI too | A Copilot session reset it to a stale `origin/main` on 2026-10-02. The leftovers include branch `backup/hmi-before-main-restore-20261002` and remote ref `bundle/feat-starting-stud`. Leave them alone unless the user says otherwise. |

## Reference files

| File | Load it when… |
|---|---|
| `references/abilities.md` | You need the file layout on the HMI, run history, recipes, logs beyond the journal, the robot web-app tunnel, Wi-Fi, or SSH fails |
| `references/rollback-and-recovery.md` | You're rolling back, the HMI has diverged, the backend won't start after a deploy, or recipes or `.env` need restoring |
| `scripts/hmi_preflight.sh` | Before every deploy (read-only) |
