#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="${WELDFLEX_UPDATE_REMOTE:-origin}"
BRANCH="${WELDFLEX_UPDATE_BRANCH:-main}"
NETWORK_CHECK_HOST="${WELDFLEX_UPDATE_NETWORK_HOST:-github.com}"
FETCH_RETRIES=5
FETCH_RETRY_DELAY=4

log() { echo "[weldflex-update] $*"; }

cd "$REPO_ROOT"

log "Checking for updates from ${REMOTE}/${BRANCH}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "Not a git repository. Skipping update check."
  exit 0
fi

# Wait for real outbound connectivity — network-online.target fires before
# DNS/routing is usable on Raspberry Pi.
log "Waiting for network connectivity to ${NETWORK_CHECK_HOST} ..."
WAIT=0
until curl -sf --max-time 5 "https://${NETWORK_CHECK_HOST}" > /dev/null 2>&1; do
  WAIT=$((WAIT + 1))
  if [[ $WAIT -ge 30 ]]; then
    log "No network after $((WAIT * 2))s. Continuing with local code."
    exit 0
  fi
  sleep 2
done
log "Network ready."

# Retry fetch — transient DNS or TLS errors are common on first boot.
FETCH_OK=false
for attempt in $(seq 1 "$FETCH_RETRIES"); do
  if git fetch "$REMOTE" "$BRANCH" --quiet 2>&1; then
    FETCH_OK=true
    break
  fi
  log "Fetch attempt $attempt/$FETCH_RETRIES failed. Retrying in ${FETCH_RETRY_DELAY}s..."
  sleep "$FETCH_RETRY_DELAY"
done

if [[ "$FETCH_OK" != "true" ]]; then
  log "All fetch attempts failed. Continuing with local code."
  exit 0
fi

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse "$REMOTE/$BRANCH")"
MERGE_BASE="$(git merge-base HEAD "$REMOTE/$BRANCH")"

if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
  log "Already up to date ($(git rev-parse --short HEAD))."
  exit 0
fi

if [[ "$LOCAL_HEAD" != "$MERGE_BASE" ]]; then
  log "Local branch is ahead or diverged. Skipping auto-update."
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  log "Working tree has local changes. Skipping auto-update."
  exit 0
fi

log "Fast-forwarding to ${REMOTE}/${BRANCH} ($(git rev-parse --short "$REMOTE/$BRANCH"))"
if ! git pull --ff-only "$REMOTE" "$BRANCH"; then
  log "Fast-forward pull failed. Continuing with local code."
  exit 0
fi

log "Updated to $(git rev-parse --short HEAD)."

if [[ -x "$REPO_ROOT/venv/bin/python" ]]; then
  log "Refreshing Python dependencies..."
  "$REPO_ROOT/venv/bin/python" -m pip install -q -r "$REPO_ROOT/requirements.txt"
  log "Dependencies refreshed."
else
  log "venv not found. Dependency refresh skipped."
fi

log "Update complete."
