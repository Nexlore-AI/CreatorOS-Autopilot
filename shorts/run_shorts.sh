#!/bin/bash
# Nexlore AI · the daily content pipeline: ideas -> scripts -> voice -> render ->
# auto-publish each video to BOTH Instagram and YouTube (Zernio).
#
# This is the only pipeline. It replaced the separate reels pipeline (run_daily.sh) on
# 2026-11-16: same house style for both platforms, SHORTS_PER_DAY videos a day.
#
#   shorts/run_shorts.sh                   full run for today (ignores the due-time gate)
#   shorts/run_shorts.sh --if-due          run only if due and not done today (launchd uses this)
#   shorts/run_shorts.sh --publish-only [DATE]   publish what is rendered but not yet published
#   shorts/run_shorts.sh --status          what has run today
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a

DATE="$(TZ=Asia/Kolkata date +%F)"; MODE="full"; GATE="no"
case "${1:-}" in
  --if-due) GATE="yes" ;;
  --publish-only) MODE="publish"; DATE="${2:-$DATE}" ;;
  --status) MODE="status" ;;
esac
N="${SHORTS_PER_DAY:-2}"
IFS=',' read -r -a TIMES <<< "${SHORTS_POST_TIMES:-09:00,13:00,18:00,21:00}"
OUT="${YOUTUBE_DIR:-$HOME/Developer/personal/NexloreAI/content/youtube}/shorts/$DATE"
IDEAS="${YOUTUBE_DIR:-$HOME/Developer/personal/NexloreAI/content/youtube}/ideas.md"
LEDGER="shorts/ledger.jsonl"; HIST="shorts/history.jsonl"; STAMP="$OUT/.published"
PY=python3; [[ -x .venv/bin/python ]] && PY="$DIR/.venv/bin/python"
mkdir -p "$OUT" logs
note() { echo "$(date '+%H:%M:%S') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"Nexlore Shorts · $DATE\"" 2>/dev/null || true; }

if [[ "$MODE" == "status" ]]; then
  echo "date (IST)   : $DATE   folder: $OUT"
  echo "due at       : $(printf '%02d:%02d' "${SHORTS_RUN_HOUR:-6}" "${SHORTS_RUN_MINUTE:-30}") local · posts at ${SHORTS_POST_TIMES:-09:00,13:00,18:00,21:00} IST"
  echo "platforms    : ${SHORTS_PLATFORMS:-instagram,youtube} · slot offset ${SHORTS_PUBLISH_OFFSET_DAYS:-1} day(s)"
  echo "status       : $(grep -m1 '^state:' "$OUT/STATUS.md" 2>/dev/null || echo 'not run')"
  echo "rendered     : $(ls -1 "$OUT"/0*_*.mp4 2>/dev/null | wc -l | tr -d ' ') of $N"
  echo "published    : $([[ -f "$STAMP" ]] && cat "$STAMP" || echo no)"
  launchctl list 2>/dev/null | grep -q com.nexloreai.shorts && echo "launchd job  : loaded" || echo "launchd job  : NOT loaded (run shorts/setup_shorts.sh)"
  exit 0
fi

if [[ "$GATE" == "yes" ]]; then
  [[ -f "$STAMP" ]] && { note "today already published"; exit 0; }
  NOW=$(( 10#$(date +%H) * 60 + 10#$(date +%M) )); DUE=$(( ${SHORTS_RUN_HOUR:-8} * 60 + ${SHORTS_RUN_MINUTE:-10} ))
  [[ $NOW -lt $DUE ]] && { note "not due yet"; exit 0; }
fi

LOCK="$DIR/.shorts.lock"
if [[ -d "$LOCK" ]]; then
  if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]]; then rmdir "$LOCK" 2>/dev/null || true
  else note "another Shorts run is in progress"; exit 0; fi
fi
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT INT TERM
exec > >(tee -a "logs/shorts-$DATE-$MODE-$(date +%H%M%S).log") 2>&1
note "shorts run: mode=$MODE date=$DATE n=$N"

# ---- 1. the agent: ideas, scripts, voice, render ----
if [[ "$MODE" == "full" ]] && ! grep -q '^state: done' "$OUT/STATUS.md" 2>/dev/null; then
  PROMPT="$(cat shorts/prompts/shorts_daily.md)"$'\n\n'"DATE=$DATE"$'\n'"OUT=$OUT"$'\n'"IDEAS=$IDEAS"$'\n'"SHORTS_PER_DAY=$N"
  set +e
  claude -p "$PROMPT" --allowedTools "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,Skill" \
    --permission-mode acceptEdits --max-turns "${SHORTS_MAX_TURNS:-120}" --output-format text
  note "claude finished rc=$?"
  set -e
fi

# ---- 2. publish each rendered video to Instagram + YouTube at its slot ----
[[ -n "${ZERNIO_API_KEY:-}" ]] || { note "ZERNIO_API_KEY missing; cannot publish"; notify "Videos rendered but not published (no Zernio key)"; exit 1; }
PLATFORMS="${SHORTS_PLATFORMS:-instagram,youtube}"
OFFSET="${SHORTS_PUBLISH_OFFSET_DAYS:-1}"
# Rendering 4 videos takes ~6 h, so they are scheduled onto a LATER day's slots and never
# race the clock. With OFFSET=0 this is same-day and a slot already past publishes now.
PUBDATE="$(TZ=Asia/Kolkata date -j -v+${OFFSET}d -f %Y-%m-%d "$DATE" +%F 2>/dev/null || echo "$DATE")"
note "publishing to $PLATFORMS · slots on $PUBDATE (offset ${OFFSET}d) · ${SHORTS_POST_TIMES}"
OK=0; FAIL=0
for i in $(seq 1 "$N"); do
  SLOT_N="$(printf '%02d' "$i")"
  VID="$(ls -1 "$OUT"/${SLOT_N}_*.mp4 2>/dev/null | grep -v '_scenes' | head -1 || true)"
  [[ -n "$VID" ]] || { note "slot $i: no video"; FAIL=$((FAIL+1)); continue; }
  # Count matches instead of piping into `grep -q`: under `set -o pipefail` the early exit
  # of `grep -q` SIGPIPEs the producer, so the pipeline reports failure even on a match and
  # an already-published video would be published a second time.
  DONE="$(grep -F "\"file\": \"$VID\"" "$LEDGER" 2>/dev/null | grep -c '"ok": true' || true)"
  if [[ "${DONE:-0}" -gt 0 ]]; then note "slot $i: already published"; OK=$((OK+1)); continue; fi

  SLOT="${TIMES[$((i-1))]:-${TIMES[${#TIMES[@]}-1]}}"
  TARGET="${PUBDATE}T${SLOT}:00"
  NOW_TS="$(TZ=Asia/Kolkata date +%s)"
  TARGET_TS="$(TZ=Asia/Kolkata date -j -f '%Y-%m-%dT%H:%M:%S' "$TARGET" +%s 2>/dev/null || echo 0)"
  if [[ "${SHORTS_PUBLISH_MODE:-auto}" == "draft" ]]; then
    ARGS=(--mode draft); WHEN="draft"
  elif [[ "$TARGET_TS" -gt "$NOW_TS" ]]; then
    ARGS=(--mode schedule --at "$TARGET" --tz Asia/Kolkata); WHEN="$TARGET"
  else
    # The slot is already behind us (a catch-up run, or OFFSET=0 late in the day).
    ARGS=(--mode now); WHEN="now"
  fi
  note "slot $i: $(basename "$VID") -> $PLATFORMS @ $WHEN"
  set +e
  RES="$("$PY" publish_zernio.py "$VID" --platforms "$PLATFORMS" --yt-visibility public "${ARGS[@]}" 2>&1)"; RC=$?
  set -e
  echo "$RES" | tail -30
  TITLE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['platforms']['youtube']['title'])" "${VID%.mp4}.json" 2>/dev/null || basename "$VID")"
  printf '{"ts": "%s", "date": "%s", "slot": %s, "file": "%s", "mode": "%s", "platforms": "%s", "when": "%s", "ok": %s, "title": %s}\n' \
    "$(date -u +%FT%TZ)" "$DATE" "$i" "$VID" "${ARGS[1]}" "$PLATFORMS" "$WHEN" \
    "$([[ $RC -eq 0 ]] && echo true || echo false)" \
    "$("$PY" -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$TITLE")" >> "$LEDGER"
  if [[ $RC -eq 0 ]]; then
    OK=$((OK+1))
    printf '{"date": "%s", "slot": %s, "title": %s, "spec": "%s"}\n' "$DATE" "$i" "$("$PY" -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$TITLE")" "${VID%.mp4}.json" >> "$HIST"
  else FAIL=$((FAIL+1)); fi
done
if [[ $OK -eq $N ]]; then echo "$(date '+%F %T') $OK/$N published to $PLATFORMS for $PUBDATE (${SHORTS_POST_TIMES} IST)" > "$STAMP"; fi
note "published $OK/$N, failed $FAIL"
notify "$OK/$N videos queued for $PLATFORMS$([[ $FAIL -gt 0 ]] && echo " · $FAIL failed, see logs")"
