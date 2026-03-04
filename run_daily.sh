#!/bin/zsh
set -uo pipefail

PROJECT_DIR="/Users/darulislah/Scripts/masjidal_to_drive"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
MODE="${1:-primary}"
LOG_FILE="$LOG_DIR/daily_run.log"
OUT_LOG="$LOG_DIR/launchd.out.log"
ERR_LOG="$LOG_DIR/launchd.err.log"
SUCCESS_FILE="$LOG_DIR/last_success_date.txt"
CLEANUP_MARKER="$LOG_DIR/.last_log_cleanup_epoch"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

rotate_log_file() {
	local file_path="$1"
	[[ -f "$file_path" ]] || return 0
	[[ -s "$file_path" ]] || return 0
	local ts
	ts=$(date +"%Y%m%d_%H%M%S")
	mv "$file_path" "${file_path:r}_$ts.log"
	: > "$file_path"
}

now_epoch=$(date +%s)
cleanup_interval=$((90 * 24 * 60 * 60))
last_cleanup=0
if [[ -f "$CLEANUP_MARKER" ]]; then
	last_cleanup=$(cat "$CLEANUP_MARKER" 2>/dev/null || echo 0)
fi

if (( now_epoch - last_cleanup >= cleanup_interval )); then
	rotate_log_file "$LOG_FILE"
	rotate_log_file "$OUT_LOG"
	rotate_log_file "$ERR_LOG"
	echo "$now_epoch" > "$CLEANUP_MARKER"
fi

today=$(date +%F)
if [[ "$MODE" == "backup" && -f "$SUCCESS_FILE" ]]; then
	if [[ "$(cat "$SUCCESS_FILE" 2>/dev/null || true)" == "$today" ]]; then
		echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup skipped: primary already succeeded today" >> "$LOG_FILE"
		exit 0
	fi
fi

echo "[$(date +"%Y-%m-%d %H:%M:%S")] run started (mode=$MODE)" >> "$LOG_FILE"

set +e
"$PYTHON_BIN" main.py >> "$LOG_FILE" 2>&1
exit_code=$?
set -e

if [[ $exit_code -eq 0 ]]; then
	echo "$today" > "$SUCCESS_FILE"
	echo "[$(date +"%Y-%m-%d %H:%M:%S")] run succeeded (mode=$MODE)" >> "$LOG_FILE"
else
	echo "[$(date +"%Y-%m-%d %H:%M:%S")] run failed with exit code $exit_code (mode=$MODE)" >> "$LOG_FILE"
fi

exit $exit_code
