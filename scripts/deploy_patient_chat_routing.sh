#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="vobiz_click_to_call"
BRANCH="${VOBIZ_DEPLOY_BRANCH:-develop}"
BENCH_DIR="${1:-${FRAPPE_BENCH_DIR:-}}"
SITE_NAME="${2:-${FRAPPE_SITE_NAME:-}}"

usage() {
  echo "Usage: $0 /path/to/frappe-bench site.name"
  echo "Example: $0 /home/frappe/frappe-bench erps.example.com"
}

if [[ -z "$BENCH_DIR" || -z "$SITE_NAME" ]]; then
  usage
  exit 2
fi

BENCH_DIR="$(cd "$BENCH_DIR" 2>/dev/null && pwd)" || {
  echo "Bench directory does not exist: $BENCH_DIR" >&2
  exit 1
}
APP_DIR="$BENCH_DIR/apps/$APP_NAME"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Git checkout not found: $APP_DIR" >&2
  exit 1
fi
if [[ ! -f "$BENCH_DIR/sites/$SITE_NAME/site_config.json" ]]; then
  echo "Frappe site not found: $BENCH_DIR/sites/$SITE_NAME" >&2
  exit 1
fi
if [[ -n "$(git -C "$APP_DIR" status --porcelain)" ]]; then
  echo "Refusing deployment: $APP_DIR has uncommitted changes." >&2
  git -C "$APP_DIR" status --short
  exit 1
fi

CURRENT_BRANCH="$(git -C "$APP_DIR" branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "Refusing deployment: expected branch '$BRANCH', found '$CURRENT_BRANCH'." >&2
  exit 1
fi

cd "$BENCH_DIR"

echo "Fetching origin/$BRANCH..."
git -C "$APP_DIR" fetch origin "$BRANCH"

LOCAL_HEAD="$(git -C "$APP_DIR" rev-parse HEAD)"
REMOTE_HEAD="$(git -C "$APP_DIR" rev-parse "origin/$BRANCH")"
if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
  echo "$APP_NAME is already at the latest origin/$BRANCH commit."
else
  git -C "$APP_DIR" merge --ff-only "origin/$BRANCH"
fi

echo "Running database migrations for $SITE_NAME..."
bench --site "$SITE_NAME" migrate

echo "Building $APP_NAME assets..."
bench build --app "$APP_NAME"

echo "Clearing Frappe caches..."
bench --site "$SITE_NAME" clear-cache
bench --site "$SITE_NAME" clear-website-cache

echo "Restarting bench services..."
bench restart

echo "Deployment completed successfully."
echo "Commit: $(git -C "$APP_DIR" rev-parse --short HEAD)"
