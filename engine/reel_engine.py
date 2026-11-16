#!/usr/bin/env python3
"""Nexlore AI reel engine — turns a JSON spec into an animated 1080x1920 MP4.

Usage:
    python3 reel_engine.py spec.json out.mp4                  # voice backend from $TTS_BACKEND (default: none)
    python3 reel_engine.py spec.json out.mp4 --voice say      # macOS built-in voice
    python3 reel_engine.py spec.json --preview preview.png    # contact sheet, no video

Voiceover: give each scene a "voice" string (what the narrator says during that scene).
Set "voice_lang": "hinglish" in the spec for Hindi-English narration (Hindi words in Devanagari, tech terms in English).
Scenes stretch automatically so the narration fits. Backends (--voice or $TTS_BACKEND):
    say         macOS `say`. $SAY_VOICE (e.g. "Ava (Premium)", "Rishi"), $SAY_RATE (words/min, default 185)
    elevenlabs  $ELEVENLABS_API_KEY (from .env / .env.local), optional $ELEVENLABS_VOICE_ID, $ELEVENLABS_MODEL
    openai      $OPENAI_API_KEY, optional $OPENAI_TTS_VOICE (default "alloy"), $OPENAI_TTS_MODEL
    none        music only

Needs: Python 3.9+, Pillow, numpy, ffmpeg on PATH. Fonts are bundled in ../fonts.
See ../specs/example_paper.json for the spec format.
"""
import json, math, os, random, shutil, subprocess, sys, tempfile, urllib.error, urllib.request, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "..", "fonts")
W, H, FPS = 1080, 1920, 30
X0 = 84
MAXW = W - X0 - 150          # keep text clear of the Reels right-hand buttons
SAFE_BOTTOM = 1560           # keep text above the Reels caption area

def font(name, size): return ImageFont.truetype(os.path.join(FONTS, name), size)
BLACK = "InterDisplay-Black.otf"; BOLD = "Inter-Bold.otf"; SEMI = "Inter-SemiBold.otf"; REG = "Inter-Regular.otf"; LOGO = "Poppins-Bold.ttf"

# Nexlore AI brand palette (see NexloreAI/brand/README.md)
def _h(x): return tuple(int(x[i:i + 2], 16) for i in (1, 3, 5))
COLORS = {
    "white": _h("#FFFFFF"), "muted": _h("#9AA0BC"), "soft": _h("#C9CDE0"),
    "cyan": _h("#0ED1D3"), "blue": _h("#298AB4"), "indigo": _h("#4C52B2"), "deepviolet": _h("#580CA3"),
    "violet": _h("#AC86D1"),          # text-safe violet on Night
    "green": _h("#10B981"), "red": _h("#F0506E"), "amber": _h("#F5A524"),
}
GRADIENT = [(0.0, _h("#0ED1D3")), (0.35, _h("#298AB4")), (0.68, _h("#4C52B2")), (1.0, _h("#580CA3"))]
BG1 = _h("#0B0A1C"); BG2 = _h("#17133A"); CARD = _h("#1F1D3F")
ASSETS = os.path.join(HERE, "..", "assets")

def gradient_bar(w, h, radius):
    """RGBA image of the signature gradient with rounded ends."""
    w = max(int(w), 1); h = max(int(h), 1)
    xs = np.linspace(0, 1, w)
    rgb = np.zeros((h, w, 3), np.float32)
    for (a, ca), (b, cb) in zip(GRADIENT, GRADIENT[1:]):
        m = (xs >= a) & (xs <= b); u = ((xs[m] - a) / (b - a))[:, None]
        rgb[:, m, :] = (np.array(ca) * (1 - u) + np.array(cb) * u)[None]
    im = Image.fromarray(rgb.astype(np.uint8)).convert("RGBA")
    mask = Image.new("L", (w, h), 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], min(radius, h // 2, w // 2), fill=255)
    im.putalpha(mask); return im

def paste_alpha(dst, im, xy, a):
    if a <= 0.01: return
    if a < 0.999:
        im = im.copy(); im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    dst.alpha_composite(im, (int(xy[0]), int(xy[1])))

def col(c, accent):
    if c in (None, "accent"): return accent
    if isinstance(c, (list, tuple)): return tuple(c)
    return COLORS.get(c, COLORS["white"])

# ---------- easing ----------
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_in_out(x): x = clamp(x); return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2
def ease_out_back(x, k=1.9): x = clamp(x); return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2
def prog(t, start, dur): return clamp((t - start) / max(dur, 1e-6))

# ---------- helpers ----------
def T(d, xy, text, f, fill, a=1.0, anchor="la"):
    if a > 0.01: d.text(xy, text, font=f, fill=fill + (int(255 * a),), anchor=anchor)

def R(d, box, r, fill, a=1.0, outline=None, width=0, oa=1.0):
    if a <= 0.01: return
    d.rounded_rectangle(box, r, fill=(fill + (int(255 * a),)) if fill else None,
                        outline=(outline + (int(255 * oa * a),)) if outline else None, width=width)

def wrap(text, f, maxw):
    words, lines, cur = str(text).split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if f.getlength(t) <= maxw: cur = t
        else:
            if cur: lines.append(cur)
            cur = w_
    if cur: lines.append(cur)
    return lines

def fit(text, fname, size, maxw, min_size=40):
    """Largest font size <= size that fits text on one line."""
    while size > min_size and font(fname, size).getlength(text) > maxw: size -= 4
    return font(fname, size)

class Warn:
    items = []
    @classmethod
    def add(cls, msg):
        if msg not in cls.items: cls.items.append(msg)

# ---------- static layers ----------
def make_bg():
    g = np.linspace(0, 1, H)[:, None]
    arr = np.zeros((H, W, 3), np.float32)
    for i in range(3): arr[..., i] = BG1[i] * (1 - g) + BG2[i] * g
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.sqrt(((xx - W / 2) / (W * 0.80)) ** 2 + ((yy - H * 0.46) / (H * 0.64)) ** 2)
    arr *= np.clip(1.0 - 0.60 * np.clip(rr - 0.52, 0, None) ** 1.5, 0.42, 1.0)[..., None]
    img = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(img)
    for x in range(0, W, 60): d.line([(x, 0), (x, H)], fill=(255, 255, 255, 4))
    for y in range(0, H, 60): d.line([(0, y), (W, y)], fill=(255, 255, 255, 4))
    return img

def make_glow(c, r):
    g = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([r, r, 3 * r, 3 * r], fill=c + (90,))
    return g.filter(ImageFilter.GaussianBlur(r * 0.6))

# ---------- scene renderers ----------
# Each gets (d, t_local, dur, A, sc, acc) where A = scene opacity, acc = accent colour.

def sc_hook(d, t, dur, A, sc, acc):
    """lines: [{text, color}], highlight: index to underline, sub: small line under.

    When this is the opening scene it is drawn complete at t=0 (no entrance). Instagram
    uses the first frame as the Reel cover, and an animating hook makes that cover blank.
    Motion in the first second comes from the background layers instead.
    """
    if sc.get("_first"): t = 99.0
    y = sc.get("y", 560)
    fl = font(BLACK, sc.get("size", 96))
    for i, ln in enumerate(sc["lines"]):
        txt = ln["text"] if isinstance(ln, dict) else ln
        c = col(ln.get("color", "white") if isinstance(ln, dict) else "white", acc)
        f = fit(txt, BLACK, fl.size, MAXW + 60)
        if f.size < fl.size: Warn.add(f"hook line shrunk to fit: {txt!r}")
        p = ease_out(prog(t, 0.15 + i * 0.28, 0.5))
        wx = X0                                   # word-by-word so the hook lands like speech
        for wi, w_ in enumerate(txt.split()):
            ws = 0.15 + i * 0.28 + wi * 0.05
            dy = (1 - ease_out_back(prog(t, ws, 0.55))) * 38
            T(d, (wx, y + dy), w_, f, c, A * ease_out(prog(t, ws, 0.40)))
            wx += f.getlength(w_ + " ")
        if i == sc.get("highlight"):
            pu = ease_out(prog(t, 0.4 + i * 0.28, 0.6))
            R(d, (X0, y + f.size + 26, X0 + f.getlength(txt) * pu, y + f.size + 36), 5, c, A * 0.85)
            y += 26
        y += int(f.size * 1.33)
    if sc.get("sub"):
        p2 = ease_out(prog(t, 0.4 + len(sc["lines"]) * 0.28, 0.6))
        for j, ln in enumerate(wrap(sc["sub"], font(SEMI, 46), MAXW)):
            T(d, (X0, y + 50 + j * 60 + (1 - p2) * 30), ln, font(SEMI, 46), COLORS["muted"], A * p2)

def sc_card(d, t, dur, A, sc, acc):
    """label, title, subtitle, meta: [[label, value], ...] — e.g. the paper / product card."""
    p = ease_out(prog(t, 0, 0.6))
    y0 = sc.get("y", 500) + (1 - p) * 80
    inner = MAXW + 20
    ft = fit(sc["title"], BLACK, 90, inner - 40)
    fs, fm = font(SEMI, 44), font(SEMI, 40)
    sub = wrap(sc.get("subtitle", ""), fs, inner - 40)
    meta_lines = [(lab, wrap(val, fm, inner - 200)) for lab, val in sc.get("meta", [])]
    h = 60 + 50 + ft.size + 50 + 60 * len(sub) + 80 + sum(56 * len(v) + 14 for _, v in meta_lines) + 30
    for k in range(4, 0, -1):
        gs = 7 * k
        R(d, (X0 - 10 - gs, y0 - gs + gs * 0.6, W - 84 + gs, y0 + h + gs + gs * 0.6), 36 + gs, (0, 0, 0), A * 0.05)
    R(d, (X0 - 10, y0, W - 84, y0 + h), 36, CARD, A * 0.92, outline=acc, width=2, oa=0.5)
    d.line([(X0 + 20, y0 + 1), (W - 114, y0 + 1)], fill=(255, 255, 255, int(26 * A)), width=2)
    T(d, (X0 + 40, y0 + 60), sc.get("label", "").upper(), font(BOLD, 32), acc, A)
    T(d, (X0 + 40, y0 + 110), sc["title"], ft, COLORS["white"], A)
    yy = y0 + 110 + ft.size + 50
    for ln in sub: T(d, (X0 + 40, yy), ln, fs, COLORS["soft"], A); yy += 60
    yy += 30
    d.line([(X0 + 40, yy), (W - 140, yy)], fill=(255, 255, 255, int(40 * A)), width=2)
    yy += 40
    for i, (lab, vals) in enumerate(meta_lines):
        pi = ease_out(prog(t, 0.6 + i * 0.25, 0.5))
        T(d, (X0 + 40, yy + 4), lab.upper(), font(BOLD, 30), COLORS["muted"], A * pi)
        for v in vals:
            T(d, (X0 + 200, yy), v, fm, COLORS["white"], A * pi); yy += 56
        yy += 14
    if y0 + h > SAFE_BOTTOM: Warn.add(f"card '{sc['title']}' runs into the caption area — shorten subtitle/meta")

def heading(d, t, A, sc, acc, y=420):
    p = ease_out(prog(t, 0, 0.5))
    T(d, (X0, y + (1 - p) * 40), sc.get("label", ""), font(BOLD, 32), col(sc.get("label_color"), acc), A * p)
    fh = font(BLACK, sc.get("heading_size", 70))
    lines = wrap(sc.get("heading", ""), fh, MAXW + 40)
    for i, ln in enumerate(lines):
        T(d, (X0, y + 50 + i * int(fh.size * 1.2) + (1 - p) * 40), ln, fh, COLORS["white"], A * p)
    return y + 50 + len(lines) * int(fh.size * 1.2) + 70

def sc_list(d, t, dur, A, sc, acc):
    """label, heading, items: [str], numbered cards appear one by one. meter: optional label for a filling bar."""
    y = heading(d, t, A, sc, acc)
    c = col(sc.get("item_color"), acc)
    fc = font(SEMI, 44)
    n = len(sc["items"])
    step = min(0.9, max(0.4, (dur - 1.8) / max(n, 1)))
    for i, it in enumerate(sc["items"]):
        pi = ease_out(prog(t, 0.8 + i * step, 0.55))
        x = X0 + (1 - ease_out_back(prog(t, 0.8 + i * step, 0.7))) * 120
        lines = wrap(it, fc, MAXW - 190)
        h = 66 + 56 * len(lines)
        for k in range(3, 0, -1):
            gs = 6 * k
            R(d, (x - gs, y - gs + gs * 0.7, x + MAXW + 60 + gs, y + h + gs + gs * 0.7), 28 + gs, (0, 0, 0), A * pi * 0.05)
        R(d, (x, y, x + MAXW + 60, y + h), 28, CARD, A * pi * 0.95, outline=c, width=2, oa=0.45)
        T(d, (x + 40, y + h / 2), f"{i + 1:02d}", font(LOGO, 52), c, A * pi, anchor="lm")
        for j, ln in enumerate(lines): T(d, (x + 150, y + 33 + j * 56), ln, fc, COLORS["white"], A * pi)
        y += h + 30
    if sc.get("meter"):
        pm = prog(t, 1.0, dur - 1.4)
        T(d, (X0, y + 30), sc["meter"].upper(), font(BOLD, 32), COLORS["muted"], A)
        R(d, (X0, y + 80, W - 140, y + 110), 15, (255, 255, 255), A * 0.08)
        R(d, (X0, y + 80, X0 + (W - 140 - X0) * ease_in_out(pm), y + 110), 15, c, A * 0.9)
        y += 110
    if y > SAFE_BOTTOM: Warn.add(f"list '{sc.get('heading')}' runs into the caption area — fewer/shorter items")

def sc_flow(d, t, dur, A, sc, acc):
    """label, heading, nodes: [str, str, str] drawn top-to-bottom with arrows, bullets: [str]."""
    y = heading(d, t, A, sc, acc) - 20
    fch = font(SEMI, 40)
    nodes = sc.get("nodes", [])
    cx = X0 + (MAXW + 60) / 2
    for i, lab in enumerate(nodes):
        pi = ease_out(prog(t, 0.5 + i * 0.45, 0.5))
        c = COLORS["muted"] if i == 0 else (COLORS["green"] if i == len(nodes) - 1 else acc)
        bw = min(MAXW + 60, fch.getlength(lab) + 80)
        R(d, (cx - bw / 2, y, cx + bw / 2, y + 96), 48, CARD, A * pi, outline=c, width=3, oa=0.9)
        T(d, (cx, y + 48), lab, fch, COLORS["white"] if i == 0 else c, A * pi, anchor="mm")
        if i < len(nodes) - 1:
            pa = ease_out(prog(t, 0.8 + i * 0.45, 0.4))
            d.line([(cx, y + 104), (cx, y + 104 + 56 * pa)], fill=acc + (int(200 * A * pa),), width=5)
            if pa > 0.95: d.polygon([(cx - 14, y + 150), (cx + 14, y + 150), (cx, y + 166)], fill=acc + (int(220 * A),))
        y += 176
    y += 20
    fb = font(SEMI, 42)
    for i, b in enumerate(sc.get("bullets", [])):
        pi = ease_out(prog(t, 0.6 + 0.45 * len(nodes) + i * 0.55, 0.5))
        d.ellipse([X0, y + 14, X0 + 30, y + 44], fill=acc + (int(255 * A * pi),))
        for j, ln in enumerate(wrap(b, fb, MAXW - 40)):
            T(d, (X0 + 56, y + (1 - pi) * 20), ln, fb, COLORS["white"], A * pi); y += 58
        y += 18
    if y > SAFE_BOTTOM: Warn.add(f"flow '{sc.get('heading')}' runs into the caption area")

def sc_stat(d, t, dur, A, sc, acc):
    """label, value (number), prefix, suffix, decimals, caption, bars: {before, after, after_label}, note."""
    c = col(sc.get("color", "green"), acc)
    p = ease_out(prog(t, 0, 0.5))
    T(d, (X0, 420), sc.get("label", ""), font(BOLD, 32), c, A * p)
    pc = ease_out(prog(t, 0.3, 1.8))
    v = float(sc["value"]) * pc
    txt = f"{sc.get('prefix', '')}{v:.{sc.get('decimals', 0)}f}{sc.get('suffix', '')}"
    final = f"{sc.get('prefix', '')}{float(sc['value']):.{sc.get('decimals', 0)}f}{sc.get('suffix', '')}"
    fb = fit(final, BLACK, 230, MAXW + 60, 120)
    gcx, gcy, pg = X0 + fb.getlength(final) / 2, 450 + fb.size * 0.52, ease_out(prog(t, 0.3, 1.8))
    for k in range(6, 0, -1):                 # soft glow behind the number (non-text layer)
        rr = 68 * k
        d.ellipse([gcx - rr * 1.5, gcy - rr * 0.8, gcx + rr * 1.5, gcy + rr * 0.8], fill=c + (int(8 * A * pg),))
    T(d, (X0 - 8, 450), txt, fb, c, A * p)
    y = 450 + int(fb.size * 1.22)
    for ln in wrap(sc.get("caption", ""), font(SEMI, 46), MAXW + 40):
        T(d, (X0, y), ln, font(SEMI, 46), COLORS["white"], A * p); y += 60
    if sc.get("bars"):
        b = sc["bars"]; full = W - 140 - X0
        pb = ease_out(prog(t, 0.6, 1.6)); y += 60
        T(d, (X0, y), b.get("before_label", "Before"), font(REG, 34), COLORS["muted"], A)
        R(d, (X0, y + 50, X0 + full, y + 110), 18, COLORS["red"], A * 0.75)
        T(d, (X0, y + 150), b.get("after_label", "After"), font(REG, 34), COLORS["muted"], A)
        ratio = float(b.get("after", 0.5)) / float(b.get("before", 1.0))
        R(d, (X0, y + 200, X0 + full * (1 - (1 - ratio) * pb), y + 260), 18, COLORS["green"], A * 0.9)
        y += 300
    pt = ease_out(prog(t, 1.6, 0.6))
    for ln in wrap(sc.get("note", ""), font(REG, 40), MAXW):
        T(d, (X0, y + 40), ln, font(REG, 40), COLORS["soft"], A * pt); y += 56
    if y > SAFE_BOTTOM: Warn.add("stat note runs into the caption area")

def sc_cta(d, t, dur, A, sc, acc):
    """label, chips: [str], lines: [str] (last one in accent), button: str. Ends with the Nexlore AI logo."""
    p = ease_out(prog(t, 0, 0.5))
    pastes = []
    y = 420
    if sc.get("chips"):
        T(d, (X0, y), sc.get("label", ""), font(BOLD, 32), acc, A * p)
        fch = font(SEMI, 40); x = X0; y += 60
        for i, c in enumerate(sc["chips"]):
            pi = ease_out(prog(t, 0.2 + i * 0.18, 0.45))
            cw = fch.getlength(c) + 64
            if x + cw > W - 120: x = X0; y += 112
            R(d, (x, y + (1 - pi) * 30, x + cw, y + 88 + (1 - pi) * 30), 44, CARD, A * pi, outline=acc, width=2, oa=0.7)
            T(d, (x + cw / 2, y + 44 + (1 - pi) * 30), c, fch, COLORS["white"], A * pi, anchor="mm")
            x += cw + 22
        y += 200
    else:
        y = 700
    pc = ease_out(prog(t, 1.0, 0.6))
    fl = font(BLACK, 76)
    lines = sc.get("lines", [])
    for i, ln in enumerate(lines):
        f = fit(ln, BLACK, 76, MAXW + 60)
        T(d, (X0, y + (1 - pc) * 30), ln, f, acc if i == len(lines) - 1 else COLORS["white"], A * pc); y += 96
    if sc.get("button"):
        pf = ease_out(prog(t, 1.7, 0.5)); fch = font(SEMI, 40)
        bw = fch.getlength(sc["button"]) + 100; y += 50
        btn = gradient_bar(bw, 100, 50)
        ImageDraw.Draw(btn).text((bw / 2, 50), sc["button"], font=fch, fill=COLORS["white"] + (255,), anchor="mm")
        pastes.append((btn, (X0, y), A * pf))
        y += 100
    # logo lock-up at the end
    pl = ease_out(prog(t, 2.0, 0.6))
    if pl > 0:
        icon = LOGO_CACHE.get(150)
        yy = max(y + 90, 1260) + (1 - pl) * 30
        pastes.append((icon, (X0, yy), A * pl))
        fw = font(BLACK, 64)
        T(d, (X0 + icon.width + 30, yy + icon.height / 2), "Nexlore AI", fw, COLORS["white"], A * pl, anchor="lm")
    return pastes

class _LogoCache(dict):
    def get(self, h):
        if h not in self:
            im = Image.open(os.path.join(ASSETS, "nexlore-icon-512.png")).convert("RGBA")
            bb = im.getbbox(); im = im.crop(bb)
            self[h] = im.resize((int(im.width * h / im.height), h), Image.LANCZOS)
        return dict.__getitem__(self, h)
LOGO_CACHE = _LogoCache()

SCENES = {"hook": sc_hook, "card": sc_card, "list": sc_list, "flow": sc_flow, "stat": sc_stat, "cta": sc_cta}


# ---------- voiceover ----------
SR = 44100

def decode_audio(path):
    """Any audio file -> mono float32 numpy at SR."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0

def _http_post(url, headers, body, out):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f: f.write(r.read())

DEVANAGARI = range(0x0900, 0x0980)

def has_devanagari(text):
    return any(ord(ch) in DEVANAGARI for ch in text)

_SAY_DEVA_CACHE = {}

def say_speaks_devanagari(voice):
    """Probe whether a macOS voice actually pronounces Devanagari.

    Several voices accept the text and silently skip it - Samantha and Rishi both do.
    Locale is not a reliable signal (Rishi is en_IN and still drops it), so measure:
    synthesise a base sentence and the same sentence plus a Hindi clause, and compare
    durations. A voice that speaks the Hindi grows by seconds; one that drops it barely
    moves. Without this check a fallback render ships a reel missing its narration.
    """
    if voice in _SAY_DEVA_CACHE:
        return _SAY_DEVA_CACHE[voice]
    base, hin = "This is a test sentence.", "This is a test sentence. पूरा बेंचमार्क चलाना महंगा है."
    def dur(txt):
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f: path = f.name
        try:
            if subprocess.run(["say", "-v", voice, "-o", path, txt], capture_output=True).returncode != 0:
                return None
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
            return float(out) if out else None
        finally:
            try: os.unlink(path)
            except OSError: pass
    a, b = dur(base), dur(hin)
    ok = bool(a and b and (b - a) > 1.0)
    _SAY_DEVA_CACHE[voice] = ok
    return ok


def tts(text, backend, out_base, lang=None):
    """Synthesise text, return path of the audio file. lang="hinglish" picks Hindi-capable voices."""
    if backend == "say":
        if not shutil.which("say"): raise SystemExit("--voice say needs macOS (the `say` command)")
        out = out_base + ".aiff"
        rate = os.environ.get("SAY_RATE", "185")
        voice = (os.environ.get("SAY_VOICE_HINGLISH", "Lekha") if lang == "hinglish" else os.environ.get("SAY_VOICE", "")).strip()
        if has_devanagari(text):
            # never ship a reel whose Hindi was silently dropped
            for cand in [voice, os.environ.get("SAY_VOICE_HINGLISH", "Lekha"), "Lekha", "Tara"]:
                if cand and say_speaks_devanagari(cand):
                    if cand != voice:
                        Warn.add(f"voice {voice!r} silently drops Devanagari; switched to {cand!r}")
                        voice = cand
                    break
            else:
                raise SystemExit("No installed macOS voice speaks Devanagari (tried Lekha, Tara). "
                                 "Install a Hindi voice in System Settings > Accessibility > Spoken Content, "
                                 "or fix the ElevenLabs key - refusing to render a reel with missing narration.")
        cmd = ["say", "-r", rate, "-o", out]
        if voice and subprocess.run(["say", "-v", voice, "-o", out, "test"], capture_output=True).returncode == 0:
            cmd[1:1] = ["-v", voice]
        elif voice:
            Warn.add(f"voice {voice!r} not installed, using the system default voice")
        subprocess.run(cmd + [text], check=True)
        return out
    if backend == "elevenlabs":
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not key:
            Warn.add("ELEVENLABS_API_KEY not set (run the ElevenLabs onboarding); using the macOS voice instead")
            return tts(text, "say", out_base, lang)
        out = out_base + ".mp3"
        vid = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
        if vid in ("", "auto"):
            vid = "EXAVITQu4vr4xnSDxMaL"   # Sarah (default female); run pick_voice.py for a Hindi-native voice
        headers = {"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
        models = [m for m in [os.environ.get("ELEVENLABS_MODEL", "").strip(), "eleven_multilingual_v2", "eleven_v3"] if m]
        last = None
        for model in dict.fromkeys(models):        # try the configured model, then known-good fallbacks
            try:
                _http_post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128", headers,
                           {"text": text, "model_id": model}, out)
                return out
            except urllib.error.HTTPError as e:
                last = f"{model}: HTTP {e.code} {e.read()[:200]!r}"
                if e.code in (401, 403): break         # bad key/permissions: no point trying other models
        raise SystemExit(f"ElevenLabs request failed ({last})")
    if backend == "openai":
        out = out_base + ".mp3"
        _http_post("https://api.openai.com/v1/audio/speech",
                   {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"},
                   {"model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "voice": os.environ.get("OPENAI_TTS_VOICE", "alloy"),
                    "input": text, "response_format": "mp3"}, out)
        return out
    if backend == "test":   # silent-ish placeholder, ~2.6 words/s, for checking timing without a real voice
        n = int(SR * max(1.0, len(text.split()) / 2.6)); t_ = np.arange(n) / SR
        sig = 0.25 * np.sin(2 * np.pi * 220 * t_) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t_))
        out = out_base + ".wav"
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((sig * 32767).astype(np.int16).tobytes())
        return out
    raise SystemExit(f"unknown voice backend {backend!r}")

XFADE = 0.32        # scene cross-fade: outgoing fades out while the incoming fades in on top
VOICE_LEAD = 0.15   # seconds of silence before narration in each scene
VOICE_TAIL = 0.45   # breathing room after it

# ---------- renderer ----------
class Reel:
    def __init__(self, spec):
        self.spec = spec
        self.acc = col(spec.get("accent", "cyan"), COLORS["cyan"])
        for sc in spec["scenes"]:
            if sc["type"] not in SCENES: raise ValueError(f"unknown scene type {sc['type']}")
        self.voice = None          # list of (start_seconds, samples) once prepared
        self.build_timeline()
        self.bg = make_bg()
        self.glow_a = make_glow(self.acc, 260)
        self.glow_v = make_glow(COLORS["deepviolet"], 300)
        rnd = random.Random(7)
        self.parts = []            # (x, y, radius, speed, phase, base_alpha) in 3 depth bands
        for cnt, rlo, rhi, slo, shi, a0 in ((44, 0.9, 1.9, 3, 9, 46), (26, 1.9, 3.2, 11, 21, 78), (10, 3.2, 5.0, 24, 40, 112)):
            for _ in range(cnt):
                self.parts.append((rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(rlo, rhi),
                                   rnd.uniform(slo, shi), rnd.random() * 6.28, a0))
        self.f_logo = font(BLACK, 42); self.f_pill = font(SEMI, 28)
        self.bar = gradient_bar(W - 84 - X0, 6, 3)

    def build_timeline(self, min_durs=None):
        self.timeline, t = [], 0.0
        for i, sc in enumerate(self.spec["scenes"]):
            sc["_first"] = (i == 0)
            dur = float(sc.get("duration", 5))
            if min_durs: dur = max(dur, min_durs[i])
            self.timeline.append((t, t + dur, sc)); t += dur
        self.dur = t

    def prepare_voice(self, backend):
        """Synthesise each scene's "voice" line and stretch scenes to fit."""
        if not backend or backend == "none": return
        lines = [sc.get("voice", "").strip() for sc in self.spec["scenes"]]
        if not any(lines): Warn.add("voice requested but no scene has a \"voice\" line"); return
        self._tmp = tempfile.mkdtemp(prefix="reelvoice_")
        clips = []
        for i, txt in enumerate(lines):
            clips.append(decode_audio(tts(txt, backend, os.path.join(self._tmp, f"s{i}"), self.spec.get("voice_lang"))) if txt else None)
        mins = [(VOICE_LEAD + len(c) / SR + VOICE_TAIL) if c is not None else 0 for c in clips]
        self.build_timeline(mins)
        self.voice = [(s + VOICE_LEAD, c) for (s, _, _), c in zip(self.timeline, clips) if c is not None]

    def chrome(self, d, ov, t):
        acc, a = self.acc, 1.0        # present from the first frame, see above
        icon = LOGO_CACHE.get(54)
        paste_alpha(ov, icon, (X0, 140), a)
        T(d, (X0 + icon.width + 18, 167), self.spec.get("brand", "Nexlore AI"), self.f_logo, COLORS["white"], a, anchor="lm")
        pill = self.spec.get("segment", "").upper()
        if pill:
            pw = self.f_pill.getlength(pill) + 48
            R(d, (W - 84 - pw, 140, W - 84, 194), 27, CARD, a * 0.9, outline=acc, width=2, oa=0.6)
            T(d, (W - 84 - pw / 2, 167), pill, self.f_pill, acc, a, anchor="mm")
        R(d, (X0, 230, W - 84, 236), 3, (255, 255, 255), 0.10)
        pw = int((W - 84 - X0) * (t / self.dur))
        if pw > 6: paste_alpha(ov, gradient_bar(pw, 6, 3), (X0, 230), 0.95)

    def frame(self, t):
        img = self.bg.copy()
        gx = int(W * 0.75 + 120 * math.sin(t * 0.35)); gy = int(500 + 120 * math.cos(t * 0.3))
        img.alpha_composite(self.glow_a, (gx - self.glow_a.width // 2, gy - self.glow_a.height // 2))
        vx = int(W * 0.2 + 140 * math.cos(t * 0.25)); vy = int(1500 + 160 * math.sin(t * 0.28))
        img.alpha_composite(self.glow_v, (vx - self.glow_v.width // 2, vy - self.glow_v.height // 2))
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
        for (px, py, r, sp, ph, a0) in self.parts:
            y = (py - sp * t) % H; a = int(a0 * (0.62 + 0.38 * math.sin(t * 1.5 + ph)))
            d.ellipse([px - r, y - r, px + r, y + r], fill=(180, 220, 255, a))
        self.chrome(d, ov, t)
        for i, (s, e, sc) in enumerate(self.timeline):
            if s - 0.05 <= t < e + XFADE + 0.05:
                # the opening scene is fully opaque at t=0 - no fade from black,
                # because that frame is the Instagram Reel cover
                A = 1.0 if i == 0 else ease_out(prog(t, s, XFADE))
                if i < len(self.timeline) - 1: A *= 1 - ease_in_out(prog(t, e, XFADE))
                for im, xy, a in (SCENES[sc["type"]](d, t - s, e - s, A, sc, self.acc) or []):
                    paste_alpha(ov, im, xy, a)
        img.alpha_composite(ov)
        return img.convert("RGB")

    def audio(self, path):
        sr = 44100; dur = self.dur; n = int(sr * dur); tt = np.arange(n) / sr
        moods = {"calm": [[110, 164.81, 220, 277.18], [98, 146.83, 196, 246.94], [87.31, 130.81, 174.61, 220], [98, 146.83, 196, 246.94]],
                 "bright": [[130.81, 196, 261.63, 329.63], [146.83, 220, 293.66, 369.99], [110, 164.81, 220, 277.18], [123.47, 185, 246.94, 311.13]]}
        chords = moods.get(self.spec.get("music", "calm"), moods["calm"])
        out = np.zeros(n); seg = dur / 8
        for k in range(8):
            a, b = int(k * seg * sr), int(min(dur, (k + 1) * seg + 0.6) * sr)
            t_ = tt[a:b] - tt[a]; env = np.minimum(1, t_ / 0.6) * np.exp(-t_ / (seg * 1.4))
            for f in chords[k % 4]: out[a:b] += 0.05 * env * (np.sin(2 * np.pi * f * t_) + 0.3 * np.sin(4 * np.pi * f * t_))
        beat = 0.6
        for k in range(int(dur / beat)):
            a = int(k * beat * sr); b = min(n, a + int(0.18 * sr)); t_ = tt[a:b] - tt[a]
            out[a:b] += 0.12 * np.exp(-t_ * 28) * np.sin(2 * np.pi * 55 * t_)
        for s, _, _ in self.timeline[1:]:
            a = max(0, int((s - 0.25) * sr)); b = min(n, a + int(0.5 * sr)); t_ = tt[a:b] - tt[a]
            out[a:b] += 0.05 * np.random.default_rng(int(s * 10)).standard_normal(b - a) * np.sin(np.pi * t_ / 0.5)
        out *= np.minimum(1, np.minimum(tt / 0.5, (dur - tt) / 1.2)); out /= max(1e-9, np.abs(out).max()) / 0.7
        if self.voice:
            vo = np.zeros(n)
            for start, clip in self.voice:
                a = int(start * sr); b = min(n, a + len(clip)); vo[a:b] += clip[: b - a]
            vo /= max(1e-9, np.abs(vo).max()) / 0.9
            # duck the music under speech (smoothed envelope of the voice)
            env = np.convolve(np.abs(vo), np.ones(4410) / 4410, mode="same")
            env = np.clip(env / max(1e-9, env.max()) * 4, 0, 1)
            out = out * (0.30 - 0.18 * env) + vo
            out /= max(1.0, np.abs(out).max() / 0.95)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes((out * 32767).astype(np.int16).tobytes())

    def render(self, out):
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "bed.wav"); self.audio(wav)
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                   "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
                   "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                   "-shortest", "-movflags", "+faststart", out]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            for i in range(int(self.dur * FPS)): p.stdin.write(self.frame(i / FPS).tobytes())
            p.stdin.close()
            if p.wait() != 0: raise SystemExit("ffmpeg failed")

    def cover(self, out, t=0.0):
        """Poster frame: the YouTube thumbnail, and the frame to pick as the IG cover."""
        self.frame(t).save(out, "JPEG", quality=92, optimize=True)
        return out

    def preview(self, out):
        """Contact sheet: one frame near the end of each scene (fully animated in)."""
        ts = [max(s + 0.1, e - 0.5) for s, e, _ in self.timeline]
        thumbs = [self.frame(t).resize((360, 640)) for t in ts]
        sheet = Image.new("RGB", (360 * len(thumbs), 640))
        for i, im in enumerate(thumbs): sheet.paste(im, (i * 360, 0))
        sheet.save(out)

if __name__ == "__main__":
    if len(sys.argv) < 3: sys.exit(__doc__)
    args = sys.argv[1:]
    voice = os.environ.get("TTS_BACKEND", "none")
    if "--voice" in args:
        k = args.index("--voice"); voice = args[k + 1]; del args[k:k + 2]
    spec = json.load(open(args[0]))
    reel = Reel(spec)
    if args[1] == "--preview":
        reel.preview(args[2] if len(args) > 2 else "preview.png")
    else:
        reel.prepare_voice(voice)
        reel.render(args[1])
        cov = os.path.splitext(args[1])[0] + ".cover.jpg"
        reel.cover(cov)
        print(f"cover {cov}")
    for w_ in Warn.items: print("WARNING:", w_, file=sys.stderr)
    print(f"ok {reel.dur:.1f}s" + (" with voice" if reel.voice else "") + (f" ({len(Warn.items)} warnings)" if Warn.items else ""))
