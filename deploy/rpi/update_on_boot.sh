#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="${WELDFLEX_UPDATE_REMOTE:-origin}"
BRANCH="${WELDFLEX_UPDATE_BRANCH:-main}"

cd "$REPO_ROOT"

echo "[weldflex-update] Checking for updates from ${REMOTE}/${BRANCH}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[weldflex-update] Not a git repository. Skipping update check."
  exit 0
fi

if ! git fetch "$REMOTE" "$BRANCH" --quiet; then
  echo "[weldflex-update] Git fetch failed. Continuing with local code."
  exit 0
fi

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse "$REMOTE/$BRANCH")"
MERGE_BASE="$(git merge-base HEAD "$REMOTE/$BRANCH")"

if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
  echo "[weldflex-update] Already up to date."
  exit 0
fi

if [[ "$LOCAL_HEAD" != "$MERGE_BASE" ]]; then
  echo "[weldflex-update] Local branch is ahead or diverged. Skipping auto-update."
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[weldflex-update] Working tree has local changes. Skipping auto-update."
  exit 0
fi

echo "[weldflex-update] Fast-forwarding to ${REMOTE}/${BRANCH}"
if ! git pull --ff-only "$REMOTE" "$BRANCH"; then
  echo "[weldflex-update] Fast-forward pull failed. Continuing with local code."
  exit 0
fi

if [[ -x "$REPO_ROOT/venv/bin/python" ]]; then
  echo "[weldflex-update] Refreshing Python dependencies"
  "$REPO_ROOT/venv/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt"
else
  echo "[weldflex-update] venv not found. Dependency refresh skipped."
fi

echo "[weldflex-update] Update check complete."
