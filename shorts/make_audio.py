#!/usr/bin/env python3
"""Voice + music for one Shorts spec, via ElevenLabs (key: ELEVENLABS_API_KEY in automation/.env).

    python3 shorts/make_audio.py out/.../01_slug.json

- Voice: one clip per scene -> <spec>_vo/NN.mp3, voice SHORTS_VOICE_ID (default Riya Rao, Hindi),
  model eleven_multilingual_v2, mp3_44100_128 (the 192 kbps format needs the Creator plan).
  A clip is only re-made when its line changed (hashes in <spec>_vo/manifest.json).
- Music: if the spec has no "music", picks the least recently used track in shorts/music/
  (SHORTS_MUSIC_MODE=library, default) or composes a new instrumental from spec["music_prompt"]
  (SHORTS_MUSIC_MODE=generate; uses more credits). The chosen file name is written back into the spec.
"""
import glob, hashlib, json, os, sys, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC = os.path.join(HERE, "music")
USED = os.path.join(HERE, "music_used.json")
API = "https://api.elevenlabs.io/v1"
SETTINGS = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}

def post(url, body, out, key):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, open(out + ".part", "wb") as f: f.write(r.read())
        os.replace(out + ".part", out); return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.read()[:300].decode(errors='replace')}"
    except Exception as e:
        return str(e)

def main():
    spec_path = os.path.abspath(sys.argv[1]); spec = json.load(open(spec_path))
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key: sys.exit("ELEVENLABS_API_KEY is not set (source automation/.env)")
    voice = os.environ.get("SHORTS_VOICE_ID", "").strip() or "ZI6I4a3UGgs1DXxqWjBV"
    vo = os.path.splitext(spec_path)[0] + "_vo"; os.makedirs(vo, exist_ok=True)
    man_p = os.path.join(vo, "manifest.json"); man = json.load(open(man_p)) if os.path.exists(man_p) else {}
    fails = []
    for i, sc in enumerate(spec["scenes"], 1):
        line = (sc.get("voice") or "").strip()
        if not line: fails.append(f"scene {i}: no voice line"); continue
        out = os.path.join(vo, f"{i:02d}.mp3"); h = hashlib.sha1((voice + line).encode()).hexdigest()[:16]
        if os.path.exists(out) and man.get(f"{i:02d}") == h: print(f"voice {i:02d} unchanged"); continue
        err = post(f"{API}/text-to-speech/{voice}?output_format=mp3_44100_128", {"text": line, "model_id": "eleven_multilingual_v2", "voice_settings": SETTINGS}, out, key)
        if err: fails.append(f"scene {i}: {err}"); continue
        man[f"{i:02d}"] = h; print(f"voice {i:02d} ok")
    for stale in glob.glob(os.path.join(vo, "*.mp3")):          # scenes that were removed
        n = os.path.basename(stale)[:2]
        if n.isdigit() and int(n) > len(spec["scenes"]): os.remove(stale); man.pop(n, None)
    json.dump(man, open(man_p, "w"), indent=1)

    if not spec.get("music"):
        mode = os.environ.get("SHORTS_MUSIC_MODE", "library").strip()
        if mode == "generate" and spec.get("music_prompt"):
            name = f"gen_{spec.get('id', 'short')}.mp3"
            err = post(f"{API}/music?output_format=mp3_44100_128", {"prompt": spec["music_prompt"] + " Instrumental, leaves room for a voiceover.",
                       "music_length_ms": 75000, "force_instrumental": True}, os.path.join(MUSIC, name), key)
            if err: print("!! music generation failed, using the library:", err); mode = "library"
            else: spec["music"] = name
        if not spec.get("music"):
            tracks = sorted(os.path.basename(p) for p in glob.glob(os.path.join(MUSIC, "*.mp3")))
            used = json.load(open(USED)) if os.path.exists(USED) else {}
            if tracks:
                spec["music"] = min(tracks, key=lambda t: used.get(t, ""))
                used[spec["music"]] = spec.get("id", ""); json.dump(used, open(USED, "w"), indent=1)
        json.dump(spec, open(spec_path, "w"), ensure_ascii=False, indent=1)
        print("music", spec.get("music"))
    if fails: sys.exit("voice problems:\n  " + "\n  ".join(fails))

if __name__ == "__main__":
    main()
