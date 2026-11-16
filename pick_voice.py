#!/usr/bin/env python3
"""Pick a female ElevenLabs voice that speaks Hindi + English (Hinglish), add it to your account,
test it, and save its ID to config.env as ELEVENLABS_VOICE_ID.

Run:  python3 pick_voice.py            (picks automatically)
      python3 pick_voice.py --list     (just list candidates)
Reads ELEVENLABS_API_KEY from .env / .env.local in this folder.
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.elevenlabs.io"
FALLBACK = ("EXAVITQu4vr4xnSDxMaL", "Sarah (ElevenLabs default female voice)")
TEST_LINE = "नमस्ते! मैं हूँ Nexlore AI. आज हम एक नया AI research paper आसान भाषा में समझेंगे."


def load_key():
    for name in (".env.local", ".env"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            for line in open(p):
                m = re.match(r"\s*ELEVENLABS_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
                if m: return m.group(1)
    return os.environ.get("ELEVENLABS_API_KEY", "")


def call(method, path, key, body=None, accept="application/json"):
    req = urllib.request.Request(API + path, method=method, headers={"xi-api-key": key, "Accept": accept, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return json.loads(data) if accept == "application/json" else data


def candidates(key):
    q = urllib.parse.urlencode({"gender": "female", "language": "hi", "page_size": 30, "sort": "usage_character_count_1y"})
    voices = call("GET", f"/v1/shared-voices?{q}", key).get("voices", [])
    def score(v):
        uc = (v.get("use_case") or "").lower(); desc = (v.get("descriptive") or "").lower()
        s = 0
        s += 3 if any(k in uc for k in ("narrat", "social", "informative", "educational", "news")) else 0
        s += 2 if (v.get("age") or "") in ("young", "middle_aged", "middle aged") else 0
        s += 1 if any(k in desc for k in ("confident", "professional", "clear", "warm", "upbeat", "calm")) else 0
        s += 1 if any((l.get("language") or "") == "en" for l in v.get("verified_languages") or []) else 0
        return s
    return sorted(voices, key=score, reverse=True)


def tts_test(key, voice_id):
    for model in ("eleven_multilingual_v2", "eleven_v3"):
        try:
            audio = call("POST", f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", key,
                         {"text": TEST_LINE, "model_id": model}, accept="audio/mpeg")
            out = os.path.join(HERE, "voice_test.mp3"); open(out, "wb").write(audio)
            return out, model
        except urllib.error.HTTPError as e:
            last = f"{e.code} {e.read()[:300]!r}"
    raise RuntimeError(last)


def save(voice_id, name, model):
    p = os.path.join(HERE, "config.env"); s = open(p).read()
    for k, v in (("ELEVENLABS_VOICE_ID", voice_id), ("ELEVENLABS_MODEL", model)):
        if re.search(rf"^{k}=.*$", s, flags=re.M): s = re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M)
        else: s += f"\n{k}={v}\n"
    s = re.sub(r"^# Chosen voice:.*\n", "", s, flags=re.M)
    s = s.replace(f"ELEVENLABS_VOICE_ID={voice_id}", f"# Chosen voice: {name}\nELEVENLABS_VOICE_ID={voice_id}", 1)
    open(p, "w").write(s)


def main():
    key = load_key()
    if not key: sys.exit("No ELEVENLABS_API_KEY in .env. Run the ElevenLabs onboarding first.")
    try:
        cands = candidates(key)
    except urllib.error.HTTPError as e:
        print(f"Voice library not available for this key ({e.code}); using {FALLBACK[1]}.")
        cands = []
    if "--list" in sys.argv:
        for v in cands[:15]:
            print(f"{v['voice_id']}  {v.get('name')}  · {v.get('accent')} · {v.get('age')} · {v.get('use_case')}  {v.get('preview_url', '')}")
        return
    for v in cands[:5]:
        name = f"{v.get('name')} ({v.get('accent') or 'hi'}, library)"
        try:
            vid = v["voice_id"]
            if not v.get("is_added_by_user"):
                vid = call("POST", f"/v1/voices/add/{v['public_owner_id']}/{v['voice_id']}", key, {"new_name": f"Nexlore - {v.get('name')}"}).get("voice_id", vid)
            out, model = tts_test(key, vid)
            save(vid, name, model)
            print(f"Voice: {name} → {vid} (model {model}). Test clip: {out}")
            return
        except Exception as e:  # e.g. free plans can't use library voices over the API
            print(f"Skipping {name}: {e}")
    vid, name = FALLBACK
    out, model = tts_test(key, vid)
    save(vid, name, model)
    print(f"Voice: {name} → {vid} (model {model}). Test clip: {out}")


if __name__ == "__main__":
    main()
