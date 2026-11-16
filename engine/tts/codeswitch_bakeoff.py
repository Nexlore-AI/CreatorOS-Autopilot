#!/usr/bin/env python3
"""Hinglish code-switch bake-off: renders the SAME code-switched utterance on every
candidate TTS backend and reports cost + whether the engine dropped any script.

Why: no vendor except Amazon and Cartesia documents mid-sentence Hindi/English
code-switching. The only way to settle it is to render and listen. This harness
makes the comparison apples-to-apples and catches the silent failure mode
(engine drops the script it cannot read, and you still get billed).

Detection trick, no listening required:
  render(hindi_only) , render(english_only) , render(mixed)
  if duration(mixed) ~= duration(english_only)  -> Devanagari was DROPPED
  if duration(mixed) ~= duration(hindi_only)    -> Latin English was DROPPED
  if duration(mixed) ~= sum of both             -> both scripts spoken (listen for accent)

Usage:  python3 codeswitch_bakeoff.py --backends say,azure,google,polly,sarvam,cartesia
Keys are read from ../../.env  (never hardcode).
"""
import argparse, os, pathlib, subprocess, sys, wave, contextlib

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "bakeoff_out"

# The hard case from prompts/playbooks/02-hinglish-voice.md: Hindi carries the
# sentence, English carries the technical nouns, numbers spoken as English words.
MIXED = "पूरा AI benchmark चलाना महंगा है. Random subset लगभग काफ़ी है. दिक्कत उसी लगभग में है."

# Drop probes. Each pair differs ONLY by an appended run of words in one script, with
# identical punctuation, so delta == 0.00s proves the engine discarded that script.
# A "hindi_only vs mixed" duration comparison does NOT work: an engine that drops
# Devanagari still speaks the leftover sentence punctuation, so the durations differ
# by a plausible-looking amount and the drop hides. Measured on macOS `say`:
# Samantha/Rishi give delta 0.000s (dropped), Lekha/Tara/Aman give +2.2..2.7s (spoken).
BASE_EN = "The benchmark is expensive."
PROBE_EN_PLUS_HI = BASE_EN + " पूरा चलाना महंगा है लगभग काफ़ी"
BASE_HI = "पूरा चलाना महंगा है."
PROBE_HI_PLUS_EN = BASE_HI + " random subset benchmark threshold"
CASES = {"mixed": MIXED, "base_en": BASE_EN, "en_plus_hi": PROBE_EN_PLUS_HI,
         "base_hi": BASE_HI, "hi_plus_en": PROBE_HI_PLUS_EN}

# Female Indian voices, one per backend. Sources for each are in the research notes.
VOICES = {
    "say":      ["Lekha", "Tara"],                      # offline macOS; Samantha/Rishi DROP Devanagari
    "azure":    ["hi-IN-AnanyaNeural", "hi-IN-KavyaNeural", "hi-IN-SwaraNeural"],
    "azure_hd": ["hi-IN-Kavya:MAI-Voice-2", "hi-IN-Priya:MAI-Voice-2"],
    "google":   ["hi-IN-Chirp3-HD-Kore", "hi-IN-Chirp3-HD-Leda", "hi-IN-Neural2-A"],
    "polly":    ["Kajal"],                              # documented fully-bilingual hi-IN/en-IN
    "sarvam":   ["anushka_enhi_ads", "ishita_hi_devotional"],
    "cartesia": [],                                     # fill from GET /voices?language=hi
    "smallest": ["rhea", "mishka", "meher"],
    "openai":   ["nova", "shimmer"],
}

# $ per 1M characters (list rate), plus any fixed monthly floor and the recurring
# free allowance. Cartesia and ElevenLabs are plan-gated: their free tiers carry no
# commercial licence, so the cheapest usable option is a flat monthly fee.
RATE_PER_1M = {
    "say": 0.0, "azure": 15.0, "azure_hd": 22.0, "google": 30.0,
    "polly": 16.0, "sarvam": 24.0, "cartesia": 65.0, "smallest": 19.5, "openai": 15.0,
    "elevenlabs": 0.0,
}
# Recurring monthly free characters. Polly's neural allowance only lasts 12 months.
FREE_CHARS_PER_MONTH = {
    "say": float("inf"), "azure": 500_000, "azure_hd": 500_000, "google": 1_000_000,
    "polly": 1_000_000, "sarvam": 0, "cartesia": 100_000, "smallest": 0, "openai": 0,
    "elevenlabs": 121_000,
}
# Flat monthly fee required before any character is billable (commercial licence).
PLAN_FLOOR = {"cartesia": 5.00, "elevenlabs": 22.00}
COST_NOTE = {
    "polly": "free allowance expires after 12 months, then $0.96",
    "cartesia": "Pro plan; free 20k/mo has no commercial licence",
    "elevenlabs": "baseline: Creator, 121k credits. Starter (30k) cannot hold 60k chars "
                  "on eleven_multilingual_v2 at 1 credit/char",
    "say": "offline macOS, no API, no licence cost",
}


def load_env():
    env = HERE.parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def duration(path):
    """Seconds of audio, via ffprobe (handles every container these APIs return)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def render_say(voice, text, dest):
    subprocess.run(["say", "-v", voice, "-o", str(dest), text], check=True)
    return dest


def render_azure(voice, text, dest, hd=False):
    import urllib.request
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ.get("AZURE_SPEECH_REGION", "centralindia")
    ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="hi-IN">'
            f'<voice name="{voice}">{text}</voice></speak>')
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode(),
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3"})
    dest.write_bytes(urllib.request.urlopen(req).read())
    return dest


def render_google(voice, text, dest):
    import json, urllib.request, base64
    key = os.environ["GOOGLE_TTS_API_KEY"]
    body = {"input": {"text": text},
            "voice": {"languageCode": "hi-IN", "name": voice},
            "audioConfig": {"audioEncoding": "MP3"}}
    req = urllib.request.Request(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req))
    dest.write_bytes(base64.b64decode(r["audioContent"]))
    return dest


def render_polly(voice, text, dest):
    # Kajal is the documented fully-bilingual hi-IN/en-IN voice. LanguageCode must be
    # hi-IN or Arabic numerals are read in Indian English.
    subprocess.run(["aws", "polly", "synthesize-speech", "--engine", "neural",
                    "--language-code", "hi-IN", "--voice-id", voice,
                    "--output-format", "mp3", "--text", text, str(dest)], check=True)
    return dest


def render_sarvam(voice, text, dest):
    import json, urllib.request, base64
    body = {"text": text, "target_language_code": "hi-IN", "speaker": voice,
            "model": "bulbul:v4-flash", "enable_preprocessing": True}
    req = urllib.request.Request(
        "https://api.sarvam.ai/text-to-speech", data=json.dumps(body).encode(),
        headers={"api-subscription-key": os.environ["SARVAM_API_KEY"],
                 "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req))
    dest.write_bytes(base64.b64decode(r["audios"][0]))
    return dest


def render_cartesia(voice, text, dest):
    import json, urllib.request
    body = {"model_id": "sonic-3.6", "transcript": text,
            "voice": {"mode": "id", "id": voice}, "language": "hi",
            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000}}
    req = urllib.request.Request(
        "https://api.cartesia.ai/tts/bytes", data=json.dumps(body).encode(),
        headers={"X-API-Key": os.environ["CARTESIA_API_KEY"],
                 "Cartesia-Version": "2024-11-13", "Content-Type": "application/json"})
    dest.write_bytes(urllib.request.urlopen(req).read())
    return dest


def render_smallest(voice, text, dest):
    import json, urllib.request
    body = {"text": text, "voice_id": voice, "language": "hi", "model": "lightning_v3.1_pro"}
    req = urllib.request.Request(
        "https://api.smallest.ai/waves/v1/tts", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['SMALLEST_API_KEY']}",
                 "Content-Type": "application/json", "Accept": "audio/wav"})
    dest.write_bytes(urllib.request.urlopen(req).read())
    return dest


def render_openai(voice, text, dest):
    import json, urllib.request
    body = {"model": "gpt-4o-mini-tts", "voice": voice, "input": text,
            "instructions": "Speak as an Indian woman explaining to a peer. "
                            "Natural Hinglish: Hindi sentence frame, English technical nouns."}
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                 "Content-Type": "application/json"})
    dest.write_bytes(urllib.request.urlopen(req).read())
    return dest


RENDERERS = {
    "say": render_say, "azure": render_azure,
    "azure_hd": lambda v, t, d: render_azure(v, t, d, hd=True),
    "google": render_google, "polly": render_polly, "sarvam": render_sarvam,
    "cartesia": render_cartesia, "smallest": render_smallest, "openai": render_openai,
}
EXT = {"say": "aiff", "smallest": "wav"}


def verdict(d):
    """Which script, if any, did the engine silently discard?

    Append-delta test: appending ~5 words should add >=0.8s of speech. A delta under
    0.25s means the appended script produced no audio at all -- it was discarded, and
    on Azure you are still billed for it ("Charges apply even if speech is not
    generated due to a mismatch between the selected voice language and the input
    text" -- Azure billable-characters doc).
    """
    if d["mixed"] == 0:
        return "NO AUDIO"
    dev = d["en_plus_hi"] - d["base_en"]
    lat = d["hi_plus_en"] - d["base_hi"]
    bad = []
    if dev < 0.25:
        bad.append("DROPPED DEVANAGARI")
    if lat < 0.25:
        bad.append("DROPPED LATIN ENGLISH")
    if bad:
        return f"{' + '.join(bad)}  (+{dev:.2f}s dev / +{lat:.2f}s lat)"
    return f"both scripts spoken (+{dev:.2f}s dev / +{lat:.2f}s lat) - LISTEN for accent"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="say",
                    help="comma list: " + ",".join(RENDERERS))
    ap.add_argument("--chars-per-month", type=int, default=60_000)
    args = ap.parse_args()
    load_env()
    OUT.mkdir(exist_ok=True)

    print(f"{'backend':<10} {'voice':<28} {'mix':>6}  verdict")
    print("-" * 104)
    for backend in [b.strip() for b in args.backends.split(",") if b.strip()]:
        if backend not in RENDERERS:
            print(f"!! unknown backend {backend}", file=sys.stderr)
            continue
        for voice in VOICES.get(backend) or ["<set VOICES[%s]>" % backend]:
            durs = {}
            for case, text in CASES.items():
                dest = OUT / f"{backend}__{voice.replace(':','_').replace('/','_')}__{case}.{EXT.get(backend,'mp3')}"
                try:
                    RENDERERS[backend](voice, text, dest)
                    durs[case] = duration(dest)
                except Exception as e:
                    print(f"{backend:<10} {voice:<28} FAILED: {type(e).__name__}: {e}")
                    durs = None
                    break
            if not durs:
                continue
            print(f"{backend:<10} {voice:<28} {durs['mixed']:>6.2f}  {verdict(durs)}")

    print("\nMonthly cost at %s chars:" % f"{args.chars_per_month:,}")
    rows = []
    for b in RATE_PER_1M:
        billable = max(0, args.chars_per_month - FREE_CHARS_PER_MONTH[b])
        cost = PLAN_FLOOR.get(b, 0.0) + billable / 1_000_000 * RATE_PER_1M[b]
        note = COST_NOTE.get(b, "")
        if billable == 0 and RATE_PER_1M[b] and b not in PLAN_FLOOR:
            note = ("inside free tier; " + note).rstrip("; ")
        rows.append((cost, b, note))
    for cost, b, note in sorted(rows):
        print(f"  {b:<12} ${cost:6.2f}" + (f"   {note}" if note else ""))
    print(f"\nAudio in {OUT}/ - listen to the *__mixed.* files before choosing.")


if __name__ == "__main__":
    main()
