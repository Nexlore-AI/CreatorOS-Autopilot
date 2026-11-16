#!/bin/bash
# Render a reel WITH voiceover on this Mac, installing anything missing first.
#   ./make_voice_sample.sh                      -> today's sample reel
#   ./make_voice_sample.sh path/to/spec.json    -> any spec (video is written next to it)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SPEC="${1:-out/2026-11-07/02_paper_tokeneconomizer.json}"
OUT="${SPEC%.json}.mp4"

if ! command -v ffmpeg >/dev/null; then
  if ! command -v brew >/dev/null; then
    echo "ffmpeg is needed and Homebrew isn't installed. Install Homebrew from https://brew.sh, then run this again."
    exit 1
  fi
  echo "==> Installing ffmpeg (one time, a few minutes)…"
  brew install ffmpeg
fi

if ! { [[ -x .venv/bin/python ]] && .venv/bin/python -c "import PIL, numpy" 2>/dev/null; }; then
  echo "==> Setting up Python packages in .venv (one time)…"
  rm -rf .venv
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q pillow numpy
fi

set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a   # secrets (.env*) override config
if [[ "${TTS_BACKEND:-}" == "elevenlabs" && ( -z "${ELEVENLABS_VOICE_ID:-}" || "${ELEVENLABS_VOICE_ID}" == "auto" ) && -n "${ELEVENLABS_API_KEY:-}" ]]; then
  PY=python3; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
  echo "==> Choosing a female Hinglish voice on ElevenLabs (one time)..."
  "$PY" pick_voice.py || echo "!! Voice picker failed; the default female voice will be used"
  set -a; source ./config.env; [[ -f .env ]] && source ./.env; [[ -f .env.local ]] && source ./.env.local; set +a
fi
VOICE="${TTS_BACKEND:-say}"
echo "==> Rendering $OUT with voice backend: ${VOICE}…"
bash ./reel "$SPEC" "$OUT" --voice "$VOICE"
DAY="$(basename "$(dirname "$SPEC")")"
if [[ -n "${REELS_DIR:-}" ]]; then
  mkdir -p "$REELS_DIR/$DAY"
  cp "$OUT" "$REELS_DIR/$DAY/"
  [[ -f "${SPEC%.json}.caption.txt" ]] && cp "${SPEC%.json}.caption.txt" "$REELS_DIR/$DAY/"
  echo "==> Copied to $REELS_DIR/$DAY/"
fi
echo "==> Done: $OUT"
open "$OUT"
