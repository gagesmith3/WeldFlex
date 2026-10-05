# Rollback and recovery on the HMI

Everything here writes to the production panel. Get the user's OK first, and
back up `backend/recipes.json` first. `$H` is the SSH prefix from SKILL.md.

## Roll back to an earlier commit

Find the last good commit in the HMI's own history. The reflog records every
deploy there, with its route (`pull --ff-only origin main` or
`pull --ff-only /tmp/<name>.bundle <branch>`).

```bash
$H 'cd ~/WeldFlex && git reflog -n 15 --date=iso main'
$H 'cd ~/WeldFlex && cp -p backend/recipes.json backend/recipes.json.bak-$(date +%Y%m%d-%H%M%S)-pre-rollback &&
    git reset --keep <good-sha> && git log -1 --oneline'
```

Then restart and reload the kiosk as in SKILL.md step 5.

**Use `--keep`, never `--hard`.** `--keep` moves `main` and keeps the live
`recipes.json` edit. It refuses if `recipes.json` itself differs between the two
commits. In that case, stop and ask: the fix is a manual merge of the HMI's
parts, and is never a discard.

**Recipe migrations.** `_recipes_load()` rewrites `recipes.json` the first time
a new build reads it. An older build may then misread the migrated fields, so
check with `git log <good-sha>..HEAD -- backend/app.py` and the commit messages
before rolling back. The known case: any commit before `ea479cc` reads the
migrated `retract_z` as the Search Height and starts every search ~127–152 mm
up. Rolling back past it needs
`backend/recipes.json.bak-20261001-111752-pre-retract-z` restored with it.

After a rollback the HMI is behind GitHub, and a later `git pull --ff-only origin
main` brings it forward again.

## The HMI has diverged

`--ff-only` refuses with "Not possible to fast-forward", or the preflight
reports a divergence. Causes seen so far:

- commits amended or rebased on this PC after they were bundled over
- an agent resetting the HMI to another ref (2026-10-02)
- a snapshot of uncommitted work applied there (2026-09-25)

1. Look before moving anything:
   `$H 'cd ~/WeldFlex && git log --oneline -12 && git status --short'`. Then
   compare with this PC's `git log --oneline <target> -12`.
2. If every HMI-only commit exists elsewhere under a new SHA (same subjects,
   same diff), keep a branch at the old head and move:
   `git branch backup/hmi-<YYYYMMDD> && git reset --keep <target>`.
3. If the HMI-only commits exist nowhere else, they are someone's work. Stop
   and ask the user.

## The backend won't start after a deploy

You'll see the kiosk sitting on a blank screen, with the kiosk journal
repeating "still waiting for backend". `systemctl is-active weldflex-backend`
flips between `activating` and `failed`, because `Restart=always` retries every
3 s.

1. Find the cause:
   `$H 'journalctl -u weldflex-backend -n 80 --no-pager -o cat | grep -B2 -A12 Traceback | tail -40'`.
2. The usual causes are 3.12-only code on the HMI's Python 3.11, a package
   missing because `requirements.txt` changed and pip wasn't run, or a new
   `.env` key with no usable default.
3. If the fix is a one-line config or pip step, apply it with the user's OK.
   Otherwise roll back (above), restart, and fix it on this PC.
4. Don't debug by running `python app.py` by hand. Even with the service
   stopped, that connects to the robot as a new client (ground rule 4).

## Restore `recipes.json`

The backend reads `recipes.json` from disk on every load, so a restore needs no
restart. Pick the backup with the user. The names say when each was taken and
why.

```bash
$H 'cd ~/WeldFlex && ls -1t backend/recipes.json.bak-* | head'
$H 'cd ~/WeldFlex && python3 -m json.tool backend/recipes.json.bak-<stamp> >/dev/null &&
    cp -p backend/recipes.json backend/recipes.json.bak-$(date +%Y%m%d-%H%M%S)-pre-restore &&
    cp -p backend/recipes.json.bak-<stamp> backend/recipes.json'
```

## Restore `.env`

`.env` is read at start, so a restore needs a restart while idle. The Admin
settings save (`/ui/settings/save`) also rewrites `.env`, which is how a test
robot IP once got persisted there. Back up the current one as
`.env.bak-<YYYYMMDD>-<reason>` before copying a backup over it.
