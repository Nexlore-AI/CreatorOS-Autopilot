#!/bin/bash
# One-time setup on your Mac: checks dependencies and installs the daily launchd job.
#   ./setup.sh            install/refresh the daily job
#   ./setup.sh --remove   stop the daily job
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.nexloreai.reels"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Daily job removed."; exit 0
fi

set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a   # secrets (.env*) override config
ok() { echo "  ✔ $1"; }; bad() { echo "  ✘ $1"; MISSING=1; }
MISSING=0
echo "Checking dependencies…"
command -v claude  >/dev/null && ok "claude CLI ($(command -v claude))" || bad "claude CLI not found"
command -v ffmpeg  >/dev/null && ok "ffmpeg" || bad "ffmpeg missing → brew install ffmpeg"
command -v python3 >/dev/null && ok "python3" || bad "python3 missing → brew install python"
if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import PIL, numpy" 2>/dev/null; then ok "Pillow + numpy (.venv)"
elif command -v python3 >/dev/null; then
  echo "  … creating .venv with Pillow + numpy"; python3 -m venv .venv && .venv/bin/pip install -q pillow numpy && ok "Pillow + numpy (.venv)" || bad "could not install Pillow/numpy"
fi
# Metapilot is optional: it only adds Edits review drafts. Rendering and publishing work without it.
MP="$(claude mcp list 2>/dev/null | grep -c "metapilot-native.*Connected" || true)"
[[ "${MP:-0}" -gt 0 ]] && ok "metapilot-native connected" \
  || echo "  ! metapilot-native not connected (Edits drafts will be skipped; everything else still runs)"
[[ -n "${ZERNIO_API_KEY:-}" ]] && ok "ZERNIO_API_KEY present (Instagram + YouTube publishing)" \
  || bad "ZERNIO_API_KEY missing from .env - the publish queue cannot run"
[[ -n "${ELEVENLABS_API_KEY:-}" ]] && ok "ELEVENLABS_API_KEY present" \
  || echo "  ! ELEVENLABS_API_KEY missing; reels will fall back to the macOS voice"
if [[ "${TTS_BACKEND:-none}" == "say" ]]; then
  VOICES="$(say -v '?' 2>/dev/null || true)"
  grep -q "^${SAY_VOICE%% *}" <<<"$VOICES" && ok "voice: $SAY_VOICE" || echo "  ! voice '$SAY_VOICE' not installed; the default system voice will be used"
fi
[[ $MISSING -eq 1 ]] && { echo "Fix the items above, then run ./setup.sh again."; exit 1; }

chmod +x run_daily.sh reel publish_queue.py publish_zernio.py
CLAUDE_DIR="$(dirname "$(command -v claude)")"
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/run_daily.sh</string><string>--if-due</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$CLAUDE_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$RUN_HOUR</integer>
    <key>Minute</key><integer>$RUN_MINUTE</integer>
  </dict>
  <!-- Fires when you log in / turn the laptop on. run_daily.sh --if-due then runs the day
       only if the scheduled time has already passed and today has not completed. -->
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
printf "\nDaily job installed.\n"
printf "  runs at        %02d:%02d local, every day\n" "$RUN_HOUR" "$RUN_MINUTE"
echo   "  also at login  catches up if the laptop was off at that time and the day is unfinished"
echo   "  publish mode   ${PUBLISH_MODE:-draft} -> ${PUBLISH_PLATFORMS:-instagram,youtube}"
echo
echo "  ./run_daily.sh --status     what has run today"
echo "  ./run_daily.sh              force a full run now"
echo "  launchctl kickstart -k gui/$(id -u)/$LABEL   trigger the job as launchd would"
echo
echo "The Mac must be awake. A day missed entirely is picked up at the next login."
