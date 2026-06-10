#!/usr/bin/env bash
# Create a public GitHub repo and push helm-chart-convert.
# Prerequisites: GitHub CLI authenticated (gh auth login) or GH_TOKEN set.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GH="${GH:-gh}"
if ! command -v "$GH" >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/gh" ]]; then
    GH="$HOME/.local/bin/gh"
  else
    echo "error: gh CLI not found. Install from https://cli.github.com/ or set GH=/path/to/gh" >&2
    exit 1
  fi
fi

REPO_NAME="${REPO_NAME:-helm-chart-convert}"
VISIBILITY="${VISIBILITY:-public}"

if [[ -z "${GH_TOKEN:-}" ]]; then
  if ! "$GH" auth status >/dev/null 2>&1; then
    echo "GitHub authentication required. Run one of:" >&2
    echo "  $GH auth login" >&2
    echo "  export GH_TOKEN=<personal-access-token>" >&2
    exit 1
  fi
fi

if git remote get-url origin >/dev/null 2>&1; then
  echo "Remote 'origin' already configured:"
  git remote -v
  echo "Pushing to origin..."
  git push -u origin main
  "$GH" repo view --web 2>/dev/null || true
  exit 0
fi

echo "Creating GitHub repository: $REPO_NAME ($VISIBILITY)"
"$GH" repo create "$REPO_NAME" \
  --"$VISIBILITY" \
  --source=. \
  --remote=origin \
  --push \
  --description "Audit and convert Helm charts for Helm 4 compatibility"

echo ""
echo "Done. Repository URL:"
"$GH" repo view --json url -q .url
