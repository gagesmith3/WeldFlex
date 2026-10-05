#!/usr/bin/env bash
# Read-only pre-deploy check for the WeldFlex HMI (see ../SKILL.md, "Pushing local
# updates to the HMI").
#
#   bash .claude/skills/hmi-ssh/scripts/hmi_preflight.sh [TARGET]
#
# TARGET is the local commit-ish you mean to put on the HMI (default: main). Run it
# from inside the repo on the dev PC (Git Bash). It fetches origin/main and reads
# the HMI over SSH. It writes nothing on the HMI and never talks to the robot.
#
# Overrides: HMI_HOST (default weldflex@192.168.1.132), HMI_KEY.
# Exit: 0 = nothing blocking, 1 = blocked, 2 = bad target or HMI unreadable.

set -u

TARGET="${1:-main}"
HMI_HOST="${HMI_HOST:-weldflex@192.168.1.132}"
HMI_KEY="${HMI_KEY:-$HOME/.ssh/id_ed25519_weldflex}"

blocked=0
block() { printf 'BLOCK  %s\n' "$*"; blocked=1; }
warn()  { printf 'WARN   %s\n' "$*"; }
step()  { printf 'STEP   %s\n' "$*"; }
info()  { printf 'INFO   %s\n' "$*"; }

top=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "Run this inside the WeldFlex repo."; exit 2; }
cd "$top" || exit 2

target_sha=$(git rev-parse --verify -q "${TARGET}^{commit}") || { echo "Unknown commit: $TARGET"; exit 2; }
short() { git log -1 --format='%h %s' "$1" 2>/dev/null || printf '%s (not in this repo)' "${1:0:7}"; }

# ── This PC ──────────────────────────────────────────────────────────────────
echo "== This PC"
GIT_TERMINAL_PROMPT=0 git fetch -q origin main 2>/dev/null \
    || warn "git fetch origin main failed; the GitHub answer below may be stale"
origin_sha=$(git rev-parse -q --verify origin/main)
info "target      $(short "$target_sha")"
info "origin/main $(short "$origin_sha")"
if [ "$target_sha" = "$origin_sha" ]; then
    route="GitHub (git pull --ff-only origin main)"
elif git merge-base --is-ancestor "$target_sha" "$origin_sha"; then
    route="bundle"
    warn "target is behind origin/main: 'git pull origin main' would deploy more than the target"
else
    route="bundle (target is not on GitHub main; or push it first, with the user's OK)"
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    warn "this PC has uncommitted changes; they are NOT part of what gets deployed"
fi

# The HMI runs Python 3.11 and this PC runs 3.13: syntax-check the target's backend
# with 3.11 when one is installed. Syntax only; 3.12-only library behaviour still
# needs the post-restart journal check.
py311=()
if command -v py >/dev/null 2>&1 && py -3.11 -c "" >/dev/null 2>&1; then
    py311=(py -3.11)
elif command -v python3.11 >/dev/null 2>&1; then
    py311=(python3.11)
fi
if [ ${#py311[@]} -gt 0 ]; then
    bad=$("${py311[@]}" - "$target_sha" <<'PY'
import ast, subprocess, sys
sha = sys.argv[1]
names = subprocess.run(["git", "ls-tree", "-r", "--name-only", sha, "--", "backend"],
                       capture_output=True, text=True, check=True).stdout.split()
for name in names:
    if name.endswith(".py"):
        src = subprocess.run(["git", "show", f"{sha}:{name}"], capture_output=True, check=True).stdout
        try:
            ast.parse(src, name)
        except SyntaxError as e:
            print(f"{name}:{e.lineno}: {e.msg}")
PY
)
    if [ -n "$bad" ]; then
        block "target's backend does not parse under Python 3.11 (the HMI's version):"
        printf '         %s\n' "$bad"
    else
        info "backend/*.py parses under Python 3.11"
    fi
else
    warn "no Python 3.11 here (py -3.11 / python3.11); skipped the HMI-version syntax check"
fi

# ── The HMI ──────────────────────────────────────────────────────────────────
echo ""
echo "== HMI ($HMI_HOST)"
remote=$(ssh -i "$HMI_KEY" -o BatchMode=yes -o ConnectTimeout=8 "$HMI_HOST" 'bash -s' 2>&1 <<'EOF'
cd ~/WeldFlex || { echo "error=no ~/WeldFlex"; exit 3; }
echo "head=$(git rev-parse HEAD)"
echo "branch=$(git rev-parse --abbrev-ref HEAD)"
git status --porcelain | sed 's/^/dirty=/'
echo "service=$(systemctl is-active weldflex-backend)"
echo "since=$(systemctl show weldflex-backend -p ActiveEnterTimestamp --value)"
job=$(curl -s -m 5 localhost:5000/ui/job/status)
echo "poll=$(printf '%s' "$job" | grep -o 'every [0-9]*ms' | head -1)"
echo "queued=$(printf '%s' "$job" | grep -c 'Job loaded')"
echo "serving=$(curl -s -m 5 localhost:5000/operator | grep -o 'BETA [0-9a-f]*' | head -1 | cut -d' ' -f2)"
echo "eth0=$(ip -4 -br addr show eth0 2>/dev/null | awk '{print $2, $3}')"
EOF
)
get() { printf '%s\n' "$remote" | sed -n "s/^$1=//p" | head -1; }

hmi_head=$(get head)
if [ -z "$hmi_head" ]; then
    printf '%s\n' "$remote" | sed 's/^/         /'
    echo "BLOCK  could not read the HMI; see references/abilities.md, \"When SSH fails\""
    exit 2
fi
branch=$(get branch); service=$(get service); since=$(get since)
poll=$(get poll); queued=$(get queued); serving=$(get serving); eth0=$(get eth0)

info "HEAD        $(short "$hmi_head")"
[ "$branch" = "main" ] || warn "HMI is on '$branch', not main"
info "backend     $service since $since"

if [ -z "$serving" ]; then
    warn "backend is not answering on :5000"
elif [ "${hmi_head#"$serving"}" = "$hmi_head" ]; then
    warn "the running backend serves BETA $serving but the checkout is at ${hmi_head:0:7}: pulled without a restart?"
fi

case "$poll" in
    "every 750ms")  block "a job or controller program is active (job panel polls at 750 ms); do not restart, wait for idle" ;;
    "every 3000ms") info "job state   idle" ;;
    *)              warn "could not read the job state" ;;
esac
[ "${queued:-0}" -gt 0 ] 2>/dev/null && warn "a job is loaded but not started; a restart drops it and the operator must Load it again"

case "$eth0" in
    UP*) info "robot link  eth0 $eth0" ;;
    *)   info "robot link  eth0 down or absent (robot off or unplugged); fine for a deploy, but \"idle\" then only means the HMI is tracking nothing. Ask whether the robot is moving" ;;
esac

# Dirt in the HMI checkout: the live recipes and dated backups are expected.
unexpected_tracked=(); unexpected_untracked=()
while IFS= read -r line; do
    case "$line" in
        "" | " M backend/recipes.json") ;;
        "?? "*bak*) ;;
        "?? "*) unexpected_untracked+=("$line") ;;
        *) unexpected_tracked+=("$line") ;;
    esac
done <<< "$(printf '%s\n' "$remote" | sed -n 's/^dirty=//p')"
if [ ${#unexpected_tracked[@]} -gt 0 ]; then
    block "HMI checkout has tracked changes besides recipes.json; find out whose work this is before deploying:"
    printf '         %s\n' "${unexpected_tracked[@]}"
fi
if [ ${#unexpected_untracked[@]} -gt 0 ]; then
    warn "HMI checkout has untracked files that are not backups:"
    printf '         %s\n' "${unexpected_untracked[@]}"
fi

# ── The move ─────────────────────────────────────────────────────────────────
echo ""
echo "== Move"
if ! git cat-file -e "${hmi_head}^{commit}" 2>/dev/null; then
    block "the HMI's HEAD ${hmi_head:0:7} is not in this repo; something was committed or fetched there. Inspect it before deploying"
elif [ "$hmi_head" = "$target_sha" ]; then
    info "the HMI is already at the target"
    [ -n "$serving" ] && [ "${hmi_head#"$serving"}" = "$hmi_head" ] && step "restart only: sudo -n systemctl restart weldflex-backend"
elif git merge-base --is-ancestor "$target_sha" "$hmi_head"; then
    block "the target is BEHIND the HMI: that is a rollback, see references/rollback-and-recovery.md"
elif ! git merge-base --is-ancestor "$hmi_head" "$target_sha"; then
    block "the HMI and the target have diverged, so --ff-only will refuse; see references/rollback-and-recovery.md"
else
    info "fast-forward ${hmi_head:0:7} -> ${target_sha:0:7}, route: $route"
    git log --oneline --reverse "$hmi_head..$target_sha" | sed 's/^/         /'
    files=$(git diff --name-only "$hmi_head" "$target_sha")
    has() { printf '%s\n' "$files" | grep -Eq "$1"; }

    step "back up recipes: cp -p backend/recipes.json backend/recipes.json.bak-\$(date +%Y%m%d-%H%M%S)-pre-${target_sha:0:7}"
    if has '^backend/recipes\.json$'; then
        block "the target changes backend/recipes.json, the HMI's live part library; --ff-only will refuse. Ask the user, never discard the HMI's copy"
    fi
    has '^requirements\.txt$' \
        && step "requirements.txt changed: venv/bin/pip install -r requirements.txt (before the restart)"
    has '^deploy/rpi/' \
        && step "deploy/rpi changed: sudo -n bash deploy/rpi/install_rpi_kiosk.sh (before the restart)"
    has '(^|/)\.env(\.rpi)?\.example$' \
        && step "an .env example changed: diff it, ask whether the HMI's .env needs the new key, back .env up first"
    has '^programs/.*\.lua$' \
        && info "Lua changed: the controller gets it at the next Run; tell the user to do a Dry run before a Live one"
    has '^backend/.*\.py$' \
        && info "Python changed: read the journal for a Traceback after the restart"
    step "restart: sudo -n systemctl restart weldflex-backend (only while idle)"
    has '^backend/(static|templates)/' \
        && step "UI changed: reload the kiosk after the restart: pkill -x cage"
fi

echo ""
if [ "$blocked" -ne 0 ]; then
    echo "RESULT: BLOCKED; resolve every BLOCK line first"
    exit 1
fi
echo "RESULT: nothing blocking. Get the user's OK, then follow SKILL.md steps 3-6"
exit 0
