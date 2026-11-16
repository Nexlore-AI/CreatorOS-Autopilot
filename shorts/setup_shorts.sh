#!/bin/bash
# One-time: installs the daily content job (launchd). This is the only pipeline since
# 2026-11-16 -- it makes SHORTS_PER_DAY videos a day for BOTH Instagram and YouTube, and
# replaced the separate reels job (com.nexloreai.reels), which is now unloaded.
#   shorts/setup_shorts.sh            install / refresh
#   shorts/setup_shorts.sh --remove   stop it
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"; LABEL="com.nexloreai.shorts"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ "${1:-}" == "--remove" ]]; then launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; rm -f "$PLIST"; echo "Shorts job removed."; exit 0; fi
set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a
ok() { echo "  ✔ $1"; }; bad() { echo "  ✘ $1"; MISSING=1; }; MISSING=0
echo "Checking…"
command -v claude >/dev/null && ok "claude CLI" || bad "claude CLI not found"
command -v ffmpeg >/dev/null && ok "ffmpeg" || bad "ffmpeg missing → brew install ffmpeg"
if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import PIL, numpy" 2>/dev/null; then ok "Pillow + numpy (.venv)"
else python3 -m venv .venv && .venv/bin/pip install -q pillow numpy && ok "Pillow + numpy (.venv)" || bad "could not install Pillow/numpy"; fi
[[ -n "${ELEVENLABS_API_KEY:-}" ]] && ok "ElevenLabs key" || bad "ELEVENLABS_API_KEY missing from .env"
[[ -n "${ZERNIO_API_KEY:-}" ]] && ok "Zernio key (YouTube publishing)" || bad "ZERNIO_API_KEY missing from .env"
ls .claude/skills/yt-script/hookscore.py >/dev/null 2>&1 && ok "YouTube skills (.claude/skills)" || bad "YouTube skills missing in .claude/skills"
ls shorts/music/*.mp3 >/dev/null 2>&1 && ok "music library: $(ls shorts/music/*.mp3 | wc -l | tr -d ' ') tracks" || bad "no tracks in shorts/music"
[[ $MISSING -eq 1 ]] && { echo "Fix the items above, then run this again."; exit 1; }
chmod +x shorts/run_shorts.sh shorts/short
CLAUDE_DIR="$(dirname "$(command -v claude)")"
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/shorts/run_shorts.sh</string><string>--if-due</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$CLAUDE_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>${SHORTS_RUN_HOUR:-8}</integer><key>Minute</key><integer>${SHORTS_RUN_MINUTE:-10}</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$DIR/logs/shorts.launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/shorts.launchd.err.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
printf "\nContent job installed.\n  makes %s videos daily at %02d:%02d, publishes to %s at %s IST (%s)\n" \
  "${SHORTS_PER_DAY:-4}" "${SHORTS_RUN_HOUR:-6}" "${SHORTS_RUN_MINUTE:-30}" "${SHORTS_PLATFORMS:-instagram,youtube}" "${SHORTS_POST_TIMES:-09:00,13:00,18:00,21:00}" "${SHORTS_PUBLISH_MODE:-auto}"
printf "  slots are %s day(s) ahead, so a run schedules onto a later day\n" "${SHORTS_PUBLISH_OFFSET_DAYS:-1}"
echo "  ideas file: ${YOUTUBE_DIR:-content/youtube}/ideas.md"
echo "  shorts/run_shorts.sh --status    ·   shorts/run_shorts.sh   (run now)"
