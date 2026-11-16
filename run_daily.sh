#!/bin/bash
# Nexlore AI daily run: research -> script -> render -> Edits drafts -> publish queue.
#
#   ./run_daily.sh                        full run for today (forced, ignores the due-time gate)
#   ./run_daily.sh --if-due               run only if today is due and not already done (launchd uses this)
#   ./run_daily.sh --upload-only [DATE]   re-upload to the Edits app only
#   ./run_daily.sh --publish-only [DATE]  re-run the Instagram/YouTube publish queue only
#   ./run_daily.sh --status               what has and has not run today
#
# Safe to run many times a day: a lock stops concurrent runs and a stamp file stops repeats.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a

DATE="$(TZ=Asia/Kolkata date +%F)"
MODE="full"
GATE="no"
case "${1:-}" in
  --if-due)       GATE="yes" ;;
  --upload-only)  MODE="upload";  DATE="${2:-$DATE}"; OVERRIDE="${3:-}" ;;
  --publish-only) MODE="publish"; DATE="${2:-$DATE}" ;;
  --status)       MODE="status" ;;
esac

mkdir -p "out/$DATE" logs
STAMP="out/$DATE/.run-complete"
PY=python3; [[ -x .venv/bin/python ]] && PY="$DIR/.venv/bin/python"

note() { echo "$(date '+%H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"Nexlore AI · $DATE\"" 2>/dev/null || true; }

if [[ "$MODE" == "status" ]]; then
  echo "date (IST)      : $DATE"
  echo "due at          : $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE") local"
  echo "today complete  : $([[ -f "$STAMP" ]] && cat "$STAMP" || echo 'no')"
  echo "reels rendered  : $(ls -1 "out/$DATE"/*.mp4 2>/dev/null | wc -l | tr -d ' ')"
  echo "publish mode    : ${PUBLISH_MODE:-draft} -> ${PUBLISH_PLATFORMS:-instagram,youtube}"
  if [[ -f publish_ledger.jsonl ]]; then
    echo "published pairs : $(grep -c '"ok": true' publish_ledger.jsonl | tr -d ' ')"
  fi
  JOB="$(launchctl list 2>/dev/null | grep -c com.nexloreai.reels || true)"
  [[ "${JOB:-0}" -gt 0 ]] && echo "launchd job     : loaded" || echo "launchd job     : NOT loaded (run ./setup.sh)"
  exit 0
fi

# ---- the due-time gate, so logging in does not re-run a finished day -------------
# launchd fires this at login (RunAtLoad) and at the scheduled time. The gate makes the
# login trigger a catch-up only: it runs when the scheduled time has passed and today
# has not completed, which is what happens when the laptop was off at that hour.
if [[ "$GATE" == "yes" ]]; then
  if [[ -f "$STAMP" ]]; then note "today already complete ($(cat "$STAMP")); nothing to do"; exit 0; fi
  NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
  DUE_MIN=$(( RUN_HOUR * 60 + RUN_MINUTE ))
  if [[ $NOW_MIN -lt $DUE_MIN ]]; then
    note "not due yet (now $(date +%H:%M), due $(printf '%02d:%02d' "$RUN_HOUR" "$RUN_MINUTE")); waiting for the scheduled run"
    exit 0
  fi
  note "catch-up: due time has passed and today is not complete"
fi

# ---- single-instance lock --------------------------------------------------------
LOCK="$DIR/.run.lock"
if [[ -d "$LOCK" ]]; then
  # a lock older than 3 hours is stale (a crash or a forced sleep)
  if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]]; then
    note "removing stale lock"; rmdir "$LOCK" 2>/dev/null || true
  else
    note "another run is in progress; exiting"; exit 0
  fi
fi
mkdir "$LOCK" 2>/dev/null || { note "lost the lock race; exiting"; exit 0; }
cleanup() { rmdir "$LOCK" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

LOG="logs/$DATE-$MODE-$(date +%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
note "run start: mode=$MODE date=$DATE"

# ---- step 1: the agent (research, script, render, Edits drafts) -------------------
if [[ "$MODE" == "full" || "$MODE" == "upload" ]]; then
  if ! curl -s -o /dev/null --max-time 5 http://127.0.0.1:19501/mcp; then
    # Not fatal any more: rendering and publishing do not need the Edits app.
    note "WARNING: Metapilot unreachable on 127.0.0.1:19501 - Edits drafts will be skipped"
    notify "Metapilot is not running; reels will still be made and queued."
  fi
  if [[ "$MODE" == "full" ]]; then
    PROMPT="$(cat prompts/daily_run.md)"$'\n\n'"DATE=$DATE"
  else
    PROMPT="$(cat prompts/upload_only.md)"$'\n\n'"DATE=$DATE"$'\n'"UPLOAD_MODE_OVERRIDE=${OVERRIDE:-}"
  fi
  note "claude: starting (max ${MAX_TURNS:-150} turns)"
  set +e
  claude -p "$PROMPT" \
    --allowedTools "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,mcp__metapilot-native" \
    --permission-mode acceptEdits \
    --max-turns "${MAX_TURNS:-150}" \
    --output-format text
  AGENT_RC=$?
  set -e
  note "claude: finished rc=$AGENT_RC"
fi

# ---- step 2: publish queue (Instagram + YouTube via Zernio) ----------------------
PUB_RC=0
if [[ "$MODE" == "full" || "$MODE" == "publish" ]]; then
  if [[ -z "${ZERNIO_API_KEY:-}" ]]; then
    note "ZERNIO_API_KEY not set; skipping the publish queue"
    PUB_RC=1
  elif ! ls "out/$DATE"/*.mp4 >/dev/null 2>&1; then
    note "no reels rendered for $DATE; nothing to publish"
  else
    note "publish queue: mode=${PUBLISH_MODE:-draft} platforms=${PUBLISH_PLATFORMS:-instagram,youtube}"
    set +e
    "$PY" -I publish_queue.py "$DATE" \
      --mode "${PUBLISH_MODE:-draft}" \
      --platforms "${PUBLISH_PLATFORMS:-instagram,youtube}"
    PUB_RC=$?
    set -e
    note "publish queue: finished rc=$PUB_RC"
  fi
fi

# ---- step 3: stamp and notify ----------------------------------------------------
STATUS="out/$DATE/STATUS.md"
STATE="unknown"
[[ -f "$STATUS" ]] && STATE="$(grep -m1 '^state:' "$STATUS" | sed 's/^state: *//' || echo unknown)"
COUNT="$(ls -1 "out/$DATE"/*.mp4 2>/dev/null | wc -l | tr -d ' ')"

if [[ "$MODE" == "full" && "$STATE" == "done" && "$PUB_RC" -eq 0 ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') state=$STATE reels=$COUNT publish=ok" > "$STAMP"
  note "day complete; stamped"
fi

MSG="$COUNT reels · $STATE · publish rc=$PUB_RC"
note "$MSG"
notify "$MSG"
exit 0
