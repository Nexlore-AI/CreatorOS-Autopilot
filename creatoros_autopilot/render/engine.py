#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The renderer: a JSON spec becomes a 1080x1920 MP4. House style "nexlore-v3".

    creatoros preview SPEC.json            # contact sheet SPEC.preview.jpg (one still per scene)
    creatoros render  SPEC.json [-o OUT.mp4]

Fonts and the brand icon are package data, so this works the same from a clone and from a
`pip install`. The music library is user content and comes from the workspace, never from
the package. Pulling the brand itself out of here — the wordmark at line ~211, the palette
and the icon — is Phase 3.

Voice clips are read from <spec>_vo/NN.mp3 (made by ../shorts/make_audio.py); each scene lasts
max(min_seconds, narration + 0.65 s). Music: spec["music"] = file name in ../shorts/music/ (rotated by
make_audio.py when absent). Everything is drawn at 2x and downsampled, so edges stay smooth.

House style (do not change per video): Night background with drifting grid, stars and two glows;
header = icon + "Nexlore AI" + tag pill + gradient progress line; white headlines (Inter Display Black),
cyan only for the key word or number, Haze for secondary text; glass cards; code in cyan mono chips.
See ../shorts/STYLE.md for the rules the script writer follows.

SPEC FORMAT
{
  "id": "2026-11-08_01_kv-cache", "tag": "AI EXPLAINED · KV CACHE", "music": "a_lofi_tech.mp3",
  "scenes": [ {"type": ..., "voice": "<Devanagari narration for this scene>", ...}, ... ],
  "platforms": {"youtube": {"title": "...", "description": "...", "tags": ["..."]}},
  "sources": ["https://..."]
}

SCENE TYPES (fields; * = required)
  hook     lines*[2-4 short lines], accent (index of the cyan line, optional), underline (index, optional),
           kicker (Haze line), big (gradient number/word), big_suffix
  stack    title*, title2 (cyan), items*[{name, sub}] listed TOP to BOTTOM as shown (max 5)
  section  label (e.g. "PIECE 2 / 4"), title*, subtitle (cyan), body, visual {type, ...}, formula (big white mono line), code (1-2 lines)
  predict  title*, title2 (cyan), prompt*, answer*, options*[[label, 0-1 value], ...max 4], outro
  points   label, title*, items*[str ...max 4]   numbered glass cards
  stat     label, value* (big gradient), caption*, body
  cta      line1* (cyan), line2*, sub, button (default "Follow Nexlore AI")

VISUAL TYPES (inside a section)
  vectors    tokens*[2-4], rows (default 6), add_waves (bool: show positional waves and add them)
  attention  tokens*[3-4], focus (index, default last), weights*[per token], caption, matrix [[...]] (optional)
  flow       blocks*[{name, color}] bottom->top (max 5), skips [[from, to], ...], repeat ("× N")
  bars       items*[[label, value 0-1]] (max 5), highlight (index, default 0)
  steps      items*[str] (max 5)  vertical pipeline with arrows
  compare    left*{title, items[]}, right*{title, items[]}
"""
import json, math, os, random, subprocess, sys, wave
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from ..core import media
from ..core import spec as spec_mod

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
ASSETS = os.path.join(HERE, "assets")


def music_dir():
    """The user's music library. Workspace content, so it is resolved per call.

    `CREATOROS_MUSIC_DIR` is set by the CLI from settings. There is no package-relative
    fallback on purpose: bundling music would mean shipping audio whose licence we cannot
    vouch for, and a missing library simply means a video with no bed.
    """
    return os.environ.get("CREATOROS_MUSIC_DIR", "")

SS = 2
W, H, FPS = 1080, 1920, 30
WW, HH = W * SS, H * SS
X0 = 90
SAFE_BOTTOM = 1500          # Shorts UI covers below this

def _h(x): return tuple(int(x[i:i + 2], 16) for i in (1, 3, 5))
NIGHT = _h("#0B0A1C"); WHITE = (255, 255, 255); HAZE = _h("#9AA0BC"); SOFT = _h("#C9CDE0")
CYAN = _h("#0ED1D3"); BLUE = _h("#298AB4"); INDIGO = _h("#4C52B2"); DVIOLET = _h("#580CA3")
VIOLET = _h("#AC86D1"); SURFACE = _h("#16152F"); CARD = _h("#1F1D3F"); BORDER = _h("#2E2B55")
GRAD = [(0.0, CYAN), (0.35, BLUE), (0.68, INDIGO), (1.0, DVIOLET)]
NAMED = {"cyan": CYAN, "blue": BLUE, "indigo": INDIGO, "violet": VIOLET, "green": _h("#10B981"),
         "amber": _h("#F5A524"), "red": _h("#F0506E")}
def named(c, default=INDIGO): return NAMED.get(c, default) if isinstance(c, str) else default

_fc = {}
def F(name, size):
    k = (name, size)
    if k not in _fc:
        fn = {"black": "InterDisplay-Black.otf", "bold": "Inter-Bold.otf", "semi": "Inter-SemiBold.otf",
              "reg": "Inter-Regular.otf", "mono": "DejaVuSansMono-Bold.ttf"}[name]
        _fc[k] = ImageFont.truetype(os.path.join(FONTS, fn), int(size * SS))
    return _fc[k]

def s(*v): return [int(round(x * SS)) for x in v]
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_back(x, k=1.7): x = clamp(x); return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2
def ramp(t, a, d=0.5): return ease((t - a) / d)
def C(c, a): return tuple(c) + (int(255 * clamp(a)),)
def lerp(c1, c2, k): return tuple(int(c1[i] + (c2[i] - c1[i]) * k) for i in range(3))
def gcol(u):
    u = clamp(u)
    for (a, ca), (b, cb) in zip(GRAD, GRAD[1:]):
        if u <= b: return lerp(ca, cb, (u - a) / (b - a))
    return GRAD[-1][1]

def grad_img(w, h):
    w, h = max(int(w), 1), max(int(h), 1)
    row = np.array([gcol(u) for u in np.linspace(0, 1, w)], np.uint8)
    return Image.fromarray(np.repeat(row[None, :, :], h, 0))

# ------------------------------------------------------------------ background
G = {}
def init():
    if G: return
    yy, xx = np.mgrid[0:HH, 0:WW].astype(np.float32)
    base = np.zeros((HH, WW, 3), np.float32)
    k = (yy / HH)[..., None]
    base += np.array(NIGHT, np.float32) * (1 - k) + np.array(_h("#120E33"), np.float32) * k
    def glow(cx, cy, r, col, amt):
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        return np.exp(-d ** 2 * 2.2)[..., None] * amt * (np.array(col, np.float32) - base)
    base += glow(WW * 0.55, HH * 0.12, WW * 0.75, _h("#0B4F63"), 0.55)
    base += glow(WW * 0.1, HH * 0.92, WW * 0.85, _h("#3A1478"), 0.55)
    d = np.sqrt(((xx - WW / 2) / (WW / 2)) ** 2 + ((yy - HH / 2) / (HH / 2)) ** 2)
    base *= (1 - 0.35 * np.clip(d - 0.6, 0, 1))[..., None]
    G["bg"] = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    gs = 96; G["gs"] = gs
    g = Image.new("RGBA", (WW, HH + gs * SS), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
    for x in range(0, W + gs, gs): gd.line(s(x, 0, x, H + gs), fill=(78, 72, 150, 80), width=SS)
    for y in range(0, H + 2 * gs, gs): gd.line(s(0, y, W, y), fill=(78, 72, 150, 80), width=SS)
    yy2, xx2 = np.mgrid[0:HH + gs * SS, 0:WW].astype(np.float32)
    m = np.clip(1.15 - np.sqrt(((xx2 - WW / 2) / (WW * 0.75)) ** 2 + ((yy2 - HH * 0.75) / (HH * 0.75)) ** 2), 0, 1)
    g.putalpha(Image.fromarray((np.array(g.split()[3], np.float32) * m).astype(np.uint8)))
    G["grid"] = g
    ic = Image.open(os.path.join(ASSETS, "nexlore-icon-512.png")).convert("RGBA"); G["icon"] = ic.crop(ic.getbbox())
    G["bar"] = grad_img(W * SS, 6 * SS)
    rnd = random.Random(11)
    G["stars"] = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(0.8, 2.2), rnd.uniform(0, 6.28), rnd.uniform(4, 14)) for _ in range(80)]

def icon(h):
    key = ("icon", h)
    if key not in G:
        hh = int(h * SS); ic = G["icon"]; G[key] = ic.resize((int(ic.width * hh / ic.height), hh), Image.LANCZOS)
    return G[key]

def background(T):
    im = G["bg"].copy(); gs = G["gs"]
    off = int((T * 14 * SS) % (gs * SS)); crop = G["grid"].crop((0, off, WW, off + HH))
    im.paste(crop, (0, 0), crop)
    d = ImageDraw.Draw(im, "RGBA")
    for (x, y, r, ph, sp) in G["stars"]:
        yy = (y - T * sp) % H; a = 0.25 + 0.35 * (0.5 + 0.5 * math.sin(T * 1.7 + ph))
        d.ellipse(s(x - r, yy - r, x + r, yy + r), fill=C(SOFT, a))
    return im, d

# ------------------------------------------------------------------ drawing helpers
def text(d, xy, t_, f, col, a, anchor="la"):
    """Text with real alpha (Pillow ignores fill alpha for text, so fades go through a mask)."""
    a = clamp(a)
    if a <= 0.004 or not t_: return
    xy2 = s(*xy); ml = "\n" in t_
    if a >= 0.996:
        (d.multiline_text if ml else d.text)(xy2, t_, font=f, fill=tuple(col), anchor=anchor); return
    bb = (d.multiline_textbbox if ml else d.textbbox)(xy2, t_, font=f, anchor=anchor)
    x0, y0 = int(bb[0]) - 2, int(bb[1]) - 2; w, h = int(bb[2]) - x0 + 4, int(bb[3]) - y0 + 4
    m = Image.new("L", (w, h), 0)
    (ImageDraw.Draw(m).multiline_text if ml else ImageDraw.Draw(m).text)((xy2[0] - x0, xy2[1] - y0), t_, font=f, fill=int(255 * a), anchor=anchor)
    d._image.paste(tuple(col), (x0, y0, x0 + w, y0 + h), m)

def tw(d, t_, f): return d.textlength(t_, font=f) / SS

def fit(d, t_, name, size, maxw, min_size=34):
    while size > min_size and tw(d, t_, F(name, size)) > maxw: size -= 2
    return F(name, size)

def wrap(d, t_, f, maxw):
    out, cur = [], ""
    for w_ in str(t_).split():
        tr = (cur + " " + w_).strip()
        if tw(d, tr, f) <= maxw: cur = tr
        else:
            if cur: out.append(cur)
            cur = w_
    if cur: out.append(cur)
    return out

def card(d, box, a, radius=28, border=BORDER, fill=CARD, bw=2, fa=0.82):
    if a <= 0.004: return
    x0, y0, x1, y1 = box
    d.rounded_rectangle(s(x0, y0, x1, y1), radius=radius * SS, fill=C(fill, fa * a), outline=C(border, a), width=bw * SS)
    d.line(s(x0 + radius, y0 + 1.5, x1 - radius, y0 + 1.5), fill=C(WHITE, 0.10 * a), width=SS)

def grad_text(im, xy, t_, f, a, anchor="la"):
    if a <= 0.004: return
    m = Image.new("L", (WW, HH), 0)
    ImageDraw.Draw(m).text(s(*xy), t_, font=f, fill=int(255 * clamp(a)), anchor=anchor)
    bb = m.getbbox()
    if bb: im.paste(grad_img(bb[2] - bb[0], bb[3] - bb[1]), bb[:2], m.crop(bb))

def grad_rect(im, box, radius, a):
    x0, y0, x1, y1 = s(*box)
    if x1 - x0 < 4 or a <= 0.004: return
    g = grad_img(x1 - x0, y1 - y0); m = Image.new("L", g.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, g.width - 1, g.height - 1), radius=radius * SS, fill=int(255 * clamp(a)))
    im.paste(g, (x0, y0), m)

def glow_dot(im, x, y, r, col, a):
    if a <= 0.01: return
    key = ("dot", r, col)
    if key not in G:
        R = int(r * SS * 4); m = Image.new("L", (R * 2, R * 2), 0)
        ImageDraw.Draw(m).ellipse((R - r * SS, R - r * SS, R + r * SS, R + r * SS), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(r * SS * 0.9))
        core = Image.new("L", (R * 2, R * 2), 0); q = r * SS * 0.45
        ImageDraw.Draw(core).ellipse((R - q, R - q, R + q, R + q), fill=255)
        G[key] = (Image.new("RGB", (R * 2, R * 2), col), Image.fromarray(np.maximum(np.array(m), np.array(core))))
    sol, m = G[key]
    if a < 0.99: m = m.point(lambda v: int(v * clamp(a)))
    im.paste(sol, (int(x * SS - sol.width / 2), int(y * SS - sol.height / 2)), m)

def bezier(p0, p1, p2, n=48):
    return [((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
             (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]) for u in (i / n for i in range(n + 1))]

def header(im, d, T, total, tag):
    ic = icon(46); im.paste(ic, s(X0, 86), ic)
    d.text(s(X0 + ic.width / SS + 14, 109), "Nexlore AI", font=F("black", 34), fill=WHITE, anchor="lm")
    tag = (tag or "AI EXPLAINED").upper(); f = F("bold", 21)
    w = tw(d, tag, f) + 40; x1 = W - X0
    d.rounded_rectangle(s(x1 - w, 88, x1, 130), radius=21 * SS, fill=C(CARD, 0.85), outline=C(INDIGO, 0.9), width=2 * SS)
    d.text(s(x1 - w / 2, 109), tag, font=f, fill=SOFT, anchor="mm")
    y = 160; d.rounded_rectangle(s(X0, y, W - X0, y + 6), radius=3 * SS, fill=C(BORDER, 0.9))
    wpx = int((W - 2 * X0) * SS * clamp(T / total))
    if wpx > 4: im.paste(G["bar"].crop((0, 0, wpx, 6 * SS)), s(X0, y))

def heading(d, t, A, sc, y=250):
    """label / title / subtitle / body. Returns the y below it."""
    if sc.get("label"):
        p = ramp(t, 0.0, 0.4); text(d, (X0, y + 12 * (1 - p)), sc["label"].upper(), F("bold", 28), CYAN, A * p)
    y += 42
    f = fit(d, sc.get("title", ""), "black", 88, W - 2 * X0)
    p = ramp(t, 0.08, 0.5); text(d, (X0, y + 26 * (1 - p)), sc.get("title", ""), f, WHITE, A * p); y += f.size / SS * 1.18
    if sc.get("subtitle"):
        fs = fit(d, sc["subtitle"], "black", 56, W - 2 * X0)
        p = ramp(t, 0.18, 0.5); text(d, (X0, y + 20 * (1 - p)), sc["subtitle"], fs, CYAN, A * p); y += fs.size / SS * 1.25
    y += 14
    if sc.get("body"):
        f = F("reg", 40)
        for i, ln in enumerate(wrap(d, sc["body"], f, W - 2 * X0 - 30)[:3]):
            p = ramp(t, 0.3 + 0.08 * i, 0.5); text(d, (X0, y + 18 * (1 - p)), ln, f, SOFT, A * p); y += 53
    return y

def code_chip(d, t, code, y, A, start, size=32):
    lines = code.split("\n")[:2]
    if len(lines) == 1 and "; " in lines[0] and tw(d, lines[0], F("mono", 28)) > W - 2 * X0 - 70:
        lines = [x.strip() for x in lines[0].split("; ", 1)]
    while size > 22 and max(tw(d, ln, F("mono", size)) for ln in lines) > W - 2 * X0 - 70: size -= 2
    f = F("mono", size); h = len(lines) * size * 1.5 + 54
    p = ramp(t, start, 0.45)
    card(d, (X0, y + 20 * (1 - p), W - X0, y + h + 20 * (1 - p)), A * p, radius=22, fill=SURFACE, fa=0.92)
    full = "\n".join(lines); code = full; n = int(len(full) * clamp((t - start - 0.2) / 1.2)); shown = 0; yy = y + 27 + 20 * (1 - p)
    for ln in lines:
        part = ln[:max(0, n - shown)]; shown += len(ln) + 1
        text(d, (X0 + 34, yy), part, f, CYAN, A * p)
        typing = 0 < len(part) < len(ln); done = n >= len(full) and ln is lines[-1]
        if typing or (done and int(t * 2.2) % 2 == 0):
            cx = X0 + 34 + tw(d, part, f)
            d.rectangle(s(cx + 4, yy + 2, cx + 6 + size * 0.5, yy + size * 1.1), fill=C(CYAN, 0.8 * A * p))
        yy += size * 1.5
    return y + h

def xs_for(n):
    if n <= 1: return [W / 2]
    left, right = X0 + 130, W - X0 - 130
    return [left + (right - left) * i / (n - 1) for i in range(n)]

def token_pills(d, t, A, tokens, y, start, focus=None, w_pill=None):
    xs = xs_for(len(tokens)); f = F("black", 50 if len(tokens) <= 3 else 42)
    for i, w_ in enumerate(tokens):
        pt = ramp(t, start + 0.1 * i, 0.4)
        bw = w_pill or tw(d, w_, f) + 56
        card(d, (xs[i] - bw / 2, y + 20 * (1 - pt), xs[i] + bw / 2, y + 80 + 20 * (1 - pt)), A * pt, radius=40,
             border=CYAN if i == focus else VIOLET, bw=3)
        text(d, (xs[i], y + 40 + 20 * (1 - pt)), w_, f, WHITE, A * pt, anchor="mm")
    return xs

# ------------------------------------------------------------------ visuals
def seeded(tokens, rows):
    import zlib
    r = np.random.default_rng(zlib.crc32("|".join(tokens).encode()))
    return r.uniform(-1, 1, (len(tokens), rows))

def v_vectors(im, d, t, D, A, y, v):
    tokens = v.get("tokens", ["the", "cat", "sat"])[:4]; rows = int(v.get("rows", 5 if v.get("add_waves") else 6))
    vec = seeded(tokens, rows); add = None; add_t = 99
    if v.get("add_waves"):
        y += 10; pc = ramp(t, 0.7, 0.5)
        card(d, (X0, y, W - X0, y + 200), A * pc, radius=26)
        cols = [CYAN, VIOLET, BLUE]; fr = [1.0, 2.0, 3.3]; prog = clamp((t - 0.9) / 1.4)
        for k in range(3):
            pts = [tuple(s(X0 + 40 + xi, y + 100 + 60 * math.sin(xi / 820 * 2 * math.pi * fr[k] + t * 1.6 + k)))
                   for xi in range(0, int(820 * prog) + 1, 4)]
            if len(pts) > 1: d.line(pts, fill=C(cols[k], 0.95 * A * pc), width=4 * SS, joint="curve")
        for i, x in enumerate(xs_for(len(tokens))):
            pm = ramp(t, 1.8 + 0.15 * i, 0.4)
            d.line(s(x, y + 18, x, y + 182), fill=C(WHITE, 0.25 * A * pm), width=2 * SS)
            text(d, (x, y + 182 - 22), f"pos {i}", F("bold", 24), HAZE, A * pm, anchor="mm")
        y += 235
        add_t = min(3.0, D * 0.4); text(d, (X0 - 8, y + 40), "+", F("black", 70), CYAN, A * ramp(t, add_t - 0.4, 0.4), anchor="lm")
        add = np.array([[math.sin(p / (10 ** (j / 3))) if j % 2 == 0 else math.cos(p / (10 ** ((j - 1) / 3)))
                         for j in range(rows)] for p in range(len(tokens))]) * 0.5
    t0 = 0.0 if v.get("add_waves") else 0.9; tt = t + (2.0 if v.get("add_waves") else 0)
    xs = token_pills(d, tt, A, tokens, y, t0, focus=len(tokens) - 1)
    cell = 62 if v.get("add_waves") else 74; fv = F("mono", 30 if len(tokens) <= 3 else 26)
    cw = 150 if len(tokens) <= 3 else 130
    for i in range(len(tokens)):
        for j in range(rows):
            st = t0 + 0.5 + 0.1 * i + 0.06 * j
            pc = ease_back(clamp((tt - st) / 0.4)); pa = clamp((tt - st) / 0.25)
            if pa <= 0: continue
            val = vec[i, j] + (add[i, j] * ease(clamp((t - add_t) / 0.8)) if add is not None else 0)
            val = clamp(val, -1, 1); col = gcol(1 - (val + 1) / 2)
            yy = y + 120 + j * (cell + 14); wv = cw * pc
            d.rounded_rectangle(s(xs[i] - wv / 2, yy, xs[i] + wv / 2, yy + cell), radius=16 * SS,
                                fill=C(lerp(CARD, col, 0.55), A * pa), outline=C(col, A * pa), width=2 * SS)
            text(d, (xs[i], yy + cell / 2), f"{val:+.2f}", fv, WHITE, A * pa, anchor="mm")
    return y + 120 + rows * (cell + 14)

def v_attention(im, d, t, D, A, y, v):
    tokens = v.get("tokens", ["the", "cat", "sat"])[:4]; n = len(tokens)
    focus = v.get("focus", n - 1); wts = v.get("weights") or [1.0 / n] * n
    xs = xs_for(n); ty = y + 200; f = F("black", 52 if n <= 3 else 44); bw = 230 if n <= 3 else 190
    for i, w_ in enumerate(tokens):
        pt = ramp(t, 0.7 + 0.1 * i, 0.4); foc = i == focus
        card(d, (xs[i] - bw / 2, ty, xs[i] + bw / 2, ty + 100), A * pt, radius=26, border=CYAN if foc else VIOLET, bw=4 if foc else 2)
        text(d, (xs[i], ty + 50), w_, fit(d, w_, "black", f.size // SS, bw - 30), WHITE, A * pt, anchor="mm")
        d.ellipse(s(xs[i] + bw / 2 - 30, ty - 25, xs[i] + bw / 2 + 20, ty + 25), fill=C(CYAN if foc else VIOLET, A * pt))
        text(d, (xs[i] + bw / 2 - 5, ty), "Q" if foc else "K", F("black", 28), NIGHT, A * pt, anchor="mm")
    k = 0
    for i in range(n):
        if i == focus: continue
        pa = clamp((t - 1.5 - 0.3 * k) / 0.9); k += 1
        if pa <= 0: continue
        w = wts[i] if i < len(wts) else 0.2
        p0 = (xs[focus] - 20 * (1 if i < focus else -1), ty - 10); p2 = (xs[i] + 10, ty - 10)
        p1 = ((p0[0] + p2[0]) / 2, ty - 110 - 0.18 * abs(xs[focus] - xs[i]))
        pts = bezier(p0, p1, p2)[: max(2, int(49 * ease(pa)))]
        d.line([tuple(s(*p)) for p in pts], fill=C(CYAN, (0.35 + 0.6 * w) * A), width=max(SS, int((3 + 16 * w) * SS)), joint="curve")
        if pa >= 1:
            path = bezier(p0, p1, p2, 60)
            for q in range(3):
                u = (t * 0.7 + q / 3) % 1.0; bp = path[int(u * 60)]
                glow_dot(im, bp[0], bp[1], 7 + 6 * w, CYAN, A * math.sin(math.pi * u))
    yy = ty + 130
    if v.get("caption"):
        text(d, (W / 2, yy + 20), v["caption"], fit(d, v["caption"], "semi", 34, W - 2 * X0), HAZE, A * ramp(t, 2.7, 0.4), anchor="mm"); yy += 70
    mat = v.get("matrix")
    if mat:
        cs = 92 if n <= 3 else 76; mx = W / 2 - n * cs / 2 + 40; my = yy + 40; st = min(3.3, D * 0.3)
        pm = ramp(t, st, 0.5)
        for r in range(n):
            text(d, (mx - 22, my + r * cs + cs / 2), tokens[r], F("semi", 30), SOFT, A * pm, anchor="rm")
            text(d, (mx + r * cs + cs / 2, my - 24), tokens[r], F("semi", 30), SOFT, A * pm, anchor="mm")
            for c in range(n):
                pc = ease(clamp((t - st - 0.08 * (r * n + c)) / 0.4)); val = mat[r][c] if r < len(mat) and c < len(mat[r]) else 0
                d.rounded_rectangle(s(mx + c * cs + 4, my + r * cs + 4, mx + (c + 1) * cs - 4, my + (r + 1) * cs - 4), radius=12 * SS,
                                    fill=C(lerp(CARD, CYAN, clamp(val) * pc), A * pm), outline=C(BORDER, A * pm), width=SS)
        text(d, (mx + n * cs + 30, my + cs * n / 2), "softmax\nweights", F("semi", 28), HAZE, A * pm, anchor="lm")
        yy = my + n * cs + 20
    return yy

def v_flow(im, d, t, D, A, y, v):
    blocks = v.get("blocks", [])[:5]; skips = v.get("skips", [])
    bx0, bx1 = X0 + 20, 600; hgt, gap = 90, 60; base = y + 40 + len(blocks) * (hgt + gap)
    ys = []
    for i, b in enumerate(blocks):
        col = named(b.get("color"), [INDIGO, BLUE, VIOLET, BLUE, CYAN][i % 5])
        pr = ease_back(clamp((t - 0.8 - 0.25 * i) / 0.45)); pa = clamp((t - 0.8 - 0.25 * i) / 0.25)
        yb = base - i * (hgt + gap); ys.append(yb)
        if pa <= 0: continue
        card(d, (bx0, yb - hgt * pr, bx1, yb), A * pa, radius=22, border=col, bw=3)
        text(d, ((bx0 + bx1) / 2, yb - hgt / 2 * pr), b.get("name", ""), fit(d, b.get("name", ""), "bold", 40, bx1 - bx0 - 30), WHITE, A * pa, anchor="mm")
    for k, (a_, b_) in enumerate(skips):
        if a_ >= len(ys) or b_ >= len(ys): continue
        ps = clamp((t - 2.0 - 0.4 * k) / 0.6)
        if ps <= 0: continue
        yf, yt = ys[a_] + 30, ys[b_] - hgt / 2; xr = bx1 + 90 + 30 * (k % 2)
        pts = [(bx1, yf), (xr, yf), (xr, yt), (bx1, yt)]
        tot = sum(math.dist(pts[i], pts[i + 1]) for i in range(3)); run = tot * ease(ps); acc = 0
        for i in range(3):
            seg = math.dist(pts[i], pts[i + 1])
            if acc + seg <= run: d.line(s(*pts[i], *pts[i + 1]), fill=C(CYAN, 0.9 * A), width=4 * SS)
            else:
                u = (run - acc) / seg; q = (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u)
                d.line(s(*pts[i], *q), fill=C(CYAN, 0.9 * A), width=4 * SS); break
            acc += seg
        if ps >= 1: text(d, (xr + 16, (yf + yt) / 2), "+ x", F("bold", 30), CYAN, A, anchor="lm")
    if t > 2.6 and ys:
        u = ((t - 2.6) / 1.8) % 1.0; y0_, y1_ = ys[0] + 10, ys[-1] - hgt - 10
        d.line(s(bx0 - 30, y1_, bx0 - 30, y0_), fill=C(BORDER, A), width=3 * SS)
        glow_dot(im, bx0 - 30, y0_ - (y0_ - y1_) * u, 10, CYAN, A * math.sin(math.pi * u))
    if v.get("repeat"):
        pn = ease_back(clamp((t - min(4.2, D * 0.55)) / 0.5))
        if pn > 0:
            cy = (ys[0] + ys[-1] - hgt) / 2
            glow_dot(im, W - X0 - 110, cy, 90, INDIGO, 0.25 * A * clamp(pn))
            grad_text(im, (W - X0, cy - 70), v["repeat"], F("black", int(110 * (0.6 + 0.4 * pn))), A * clamp(pn), anchor="ra")
    return base + 30

def v_bars(im, d, t, D, A, y, v):
    items = v.get("items", [])[:5]; hi = v.get("highlight", 0); y += 20
    for i, (lab, val) in enumerate(items):
        pb = ease(clamp((t - 1.0 - 0.15 * i) / 1.0)); pa = clamp((t - 1.0 - 0.15 * i) / 0.3); yy = y + i * 108
        text(d, (X0, yy + 34), str(lab), fit(d, str(lab), "bold", 44, 230), WHITE if i == hi else SOFT, A * pa, anchor="lm")
        x1 = X0 + 260
        d.rounded_rectangle(s(x1, yy + 8, W - X0, yy + 60), radius=26 * SS, fill=C(CARD, 0.9 * A * pa), outline=C(BORDER, A * pa), width=SS)
        bw = (W - X0 - x1) * clamp(float(val)) * pb
        if bw > 10:
            if i == hi: grad_rect(im, (x1, yy + 8, x1 + bw, yy + 60), 26, A * pa)
            else: d.rounded_rectangle(s(x1, yy + 8, x1 + bw, yy + 60), radius=26 * SS, fill=C(INDIGO, A * pa))
    return y + len(items) * 108

def v_steps(im, d, t, D, A, y, v, avail=None):
    items = v.get("items", [])[:5]; n = max(1, len(items)); y += 20
    avail = avail or (1400 - y)
    gap = 56; h = clamp((avail - (n - 1) * gap) / n, 96, 132)
    f = F("bold", 46 if h >= 120 else 42)
    for i, it in enumerate(items):
        pr = ease_back(clamp((t - 0.8 - 0.35 * i) / 0.45)); pa = clamp((t - 0.8 - 0.35 * i) / 0.25)
        yy = y + i * (h + gap) + 24 * (1 - pr)
        if pa <= 0: continue
        card(d, (X0 + 10, yy, W - X0 - 10, yy + h), A * pa, radius=26, border=CYAN if i == len(items) - 1 else INDIGO, bw=3)
        r = 28; cx = X0 + 40 + r; cy = yy + h / 2
        d.ellipse(s(cx - r, cy - r, cx + r, cy + r), fill=C(CYAN, 0.25 * A * pa), outline=C(CYAN, A * pa), width=2 * SS)
        text(d, (cx, cy), str(i + 1), F("black", 32), WHITE, A * pa, anchor="mm")
        text(d, (cx + r + 30, cy), it, fit(d, it, "bold", f.size // SS, W - 2 * X0 - 150), WHITE, A * pa, anchor="lm")
        if i < len(items) - 1:
            pl = ramp(t, 1.05 + 0.35 * i, 0.3); ya = yy + h + 6
            d.line(s(W / 2, ya, W / 2, ya + (gap - 14) * pl), fill=C(CYAN, 0.8 * A * pl), width=3 * SS)
            if pl >= 1: d.polygon([tuple(s(W / 2 - 10, ya + gap - 24)), tuple(s(W / 2 + 10, ya + gap - 24)), tuple(s(W / 2, ya + gap - 10))], fill=C(CYAN, A))
    return y + n * (h + gap) - gap

def v_compare(im, d, t, D, A, y, v, avail=None):
    y += 20; cw = (W - 2 * X0 - 30) / 2; avail = avail or (1400 - y)
    f = F("semi", 38); rows = []
    for side in ("left", "right"):
        rows.append([wrap(d, it, f, cw - 70)[:3] for it in v.get(side, {}).get("items", [])[:4]])
    need = 130 + max(sum(len(x) * 48 + 40 for x in r) for r in rows)
    hgt = clamp(need + 80, need, avail); y += max(0, (avail - hgt) / 3)
    for k, side in enumerate(("left", "right")):
        sd = v.get(side, {}); x0 = X0 + k * (cw + 30); col = VIOLET if k == 0 else CYAN
        pa = ramp(t, 0.8 + 0.4 * k, 0.45); dy = 24 * (1 - pa)
        card(d, (x0, y + dy, x0 + cw, y + hgt + dy), A * pa, radius=28, border=col, bw=3)
        text(d, (x0 + 34, y + 38 + dy), sd.get("title", ""), fit(d, sd.get("title", ""), "black", 50, cw - 60), col, A * pa)
        d.line(s(x0 + 34, y + 112 + dy, x0 + cw - 34, y + 112 + dy), fill=C(col, 0.35 * A * pa), width=2 * SS)
        yy = y + 140
        for i, lines in enumerate(rows[k]):
            pi = ramp(t, 1.2 + 0.4 * k + 0.2 * i, 0.4)
            d.ellipse(s(x0 + 36, yy + 14, x0 + 50, yy + 28), fill=C(col, A * pi))
            for j, ln in enumerate(lines): text(d, (x0 + 66, yy + j * 48), ln, f, WHITE, A * pi)
            yy += len(lines) * 48 + 40
    return y + hgt

VISUALS = {"vectors": v_vectors, "attention": v_attention, "flow": v_flow, "bars": v_bars, "steps": v_steps, "compare": v_compare}

# ------------------------------------------------------------------ scenes
def sc_hook(im, d, t, D, A, sc):
    lines = sc.get("lines", [])[:4]; acc = sc.get("accent", -1); ul = sc.get("underline", -1)
    size = 128 if max(len(l) for l in lines) <= 13 else 108
    y = 470 if len(lines) <= 3 else 400
    for i, ln in enumerate(lines):
        f = fit(d, ln, "black", size, W - 2 * X0)
        p = ramp(t, 0.16 * i, 0.45); text(d, (X0, y + 40 * (1 - p)), ln, f, CYAN if i == acc else WHITE, A * p)
        if i == ul:
            pu = ramp(t, 0.6, 0.5); wln = tw(d, ln, f)
            if pu > 0: im.paste(G["bar"].resize((max(1, int(wln * SS * pu)), 10 * SS)), s(X0 + 4, y + f.size / SS * 1.12))
        y += f.size / SS * 1.17
    if sc.get("kicker"):
        p = ramp(t, 0.85, 0.45); text(d, (X0, y + 50 + 30 * (1 - p)), sc["kicker"], fit(d, sc["kicker"], "black", 66, W - 2 * X0), HAZE, A * p)
    if sc.get("big"):
        pb = clamp((t - 1.3) / 0.55); k = ease_back(pb)
        if pb > 0:
            big = str(sc["big"]); fsz = int((300 if len(big) <= 2 else 200) * (0.7 + 0.3 * k))
            fb = F("black", fsz)
            glow_dot(im, X0 + 95, y + 330, 120, CYAN, 0.18 * A * pb)
            grad_text(im, (X0, y + 160), big, fb, A * clamp(pb * 2))
            if sc.get("big_suffix"):
                bwid = tw(d, big, F("black", int(300 if len(big) <= 2 else 200))) + 30
                text(d, (X0 + bwid, y + 300 + 30 * (1 - ease(pb))), sc["big_suffix"], fit(d, sc["big_suffix"], "black", 128, W - 2 * X0 - bwid), WHITE, A * ease(pb))

def sc_stack(im, d, t, D, A, sc):
    text(d, (X0, 270 + 26 * (1 - ramp(t, 0, 0.5))), sc.get("title", ""), fit(d, sc.get("title", ""), "black", 96, W - 2 * X0), WHITE, A * ramp(t, 0, 0.5))
    if sc.get("title2"):
        text(d, (X0, 385 + 26 * (1 - ramp(t, 0.15, 0.5))), sc["title2"], fit(d, sc["title2"], "black", 96, W - 2 * X0), CYAN, A * ramp(t, 0.15, 0.5))
    items = list(reversed(sc.get("items", [])[:5])); n = len(items)
    x0, x1, hgt, gap = X0 + 40, W - X0 - 40, 150, 34
    ys = [1330 - i * (hgt + gap) for i in range(n)]
    cols = [CYAN, BLUE, INDIGO, VIOLET, CYAN]
    pl = clamp((t - 0.6) / 1.4)
    if pl > 0 and n:
        yb, yt = ys[0] + hgt + 60, ys[-1]
        d.line(s(W / 2, yb, W / 2, yb - (yb - yt) * pl), fill=C(INDIGO, 0.6 * A), width=4 * SS)
    for i, it in enumerate(items):
        st = 0.55 + 0.3 * i; pi = ease_back(clamp((t - st) / 0.5)); pa = clamp((t - st) / 0.3)
        if pa <= 0: continue
        y = ys[i] + 40 * (1 - pi); col = cols[i % 5]
        card(d, (x0, y, x1, y + hgt), A * pa, radius=30, border=col, bw=3)
        d.rounded_rectangle(s(x0 + 30, y + 40, x0 + 100, y + 110), radius=18 * SS, fill=C(col, 0.22 * A * pa), outline=C(col, A * pa), width=2 * SS)
        text(d, (x0 + 65, y + 75), str(i + 1), F("black", 40), WHITE, A * pa, anchor="mm")
        text(d, (x0 + 130, y + 38), it.get("name", ""), fit(d, it.get("name", ""), "bold", 46, x1 - x0 - 160), WHITE, A * pa)
        text(d, (x0 + 130, y + 96), it.get("sub", ""), fit(d, it.get("sub", ""), "reg", 32, x1 - x0 - 160), HAZE, A * pa)
    if t > 2.0 and n:
        u = ((t - 2.0) / 1.6) % 1.0; yb, yt = ys[0] + hgt + 60, ys[-1] - 30
        glow_dot(im, W / 2, yb - (yb - yt) * u, 12, CYAN, A * 0.9 * math.sin(math.pi * u))

def sc_section(im, d, t, D, A, sc):
    y = heading(d, t, A, sc, 250)
    v = sc.get("visual")
    if v and v.get("type") in VISUALS:
        if v["type"] in ("steps", "compare"):
            room = (1430 if not sc.get("code") else 1290) - (y + 30) - (100 if sc.get("formula") else 0)
            y = VISUALS[v["type"]](im, d, t, D, A, y + 30, v, avail=room)
        else:
            y = VISUALS[v["type"]](im, d, t, D, A, y + 30, v)
    if sc.get("formula"):
        pf = ramp(t, min(5.5, D * 0.5), 0.5); ff = fit(d, sc["formula"], "mono", 54, W - 2 * X0)
        text(d, (W / 2, y + 50 + 24 * (1 - pf)), sc["formula"], ff, WHITE, A * pf, anchor="mm"); y += 100
    if sc.get("code"):
        start = min(3.0 if not v else (6.6 if sc.get("formula") else 4.6), D * 0.6)
        code_chip(d, t, sc["code"], y + 30, A, start)

def sc_predict(im, d, t, D, A, sc):
    text(d, (X0, 260 + 26 * (1 - ramp(t, 0, 0.5))), sc.get("title", ""), fit(d, sc.get("title", ""), "black", 84, W - 2 * X0), WHITE, A * ramp(t, 0, 0.5))
    if sc.get("title2"):
        text(d, (X0, 365 + 20 * (1 - ramp(t, 0.25, 0.5))), sc["title2"], fit(d, sc["title2"], "black", 84, W - 2 * X0), CYAN, A * ramp(t, 0.25, 0.5))
    y = 540; pc = ramp(t, 0.7, 0.5)
    card(d, (X0, y, W - X0, y + 130), A * pc, radius=30, border=INDIGO, bw=3)
    f = fit(d, sc.get("prompt", "") + " " + sc.get("answer", ""), "semi", 54, W - 2 * X0 - 100)
    text(d, (X0 + 40, y + 65), sc.get("prompt", ""), f, WHITE, A * pc, anchor="lm")
    x = X0 + 40 + tw(d, sc.get("prompt", "") + " ", f)
    reveal = min(3.6, D * 0.55); pr = clamp((t - reveal) / 0.4)
    if pr <= 0:
        if int(t * 2.4) % 2 == 0 and pc > 0.5: d.rectangle(s(x, y + 35, x + 6, y + 95), fill=C(CYAN, A * pc))
    else:
        text(d, (x, y + 65 - 14 * (1 - ease_back(pr))), sc.get("answer", ""), F("black", f.size // SS + 4), CYAN, A * pr, anchor="lm")
    y = v_bars(im, d, t, D, A, y + 170, {"items": sc.get("options", []), "highlight": 0})
    if sc.get("outro"):
        pe = ramp(t, min(4.4, D * 0.7), 0.5)
        text(d, (X0, y + 50 + 20 * (1 - pe)), sc["outro"], fit(d, sc["outro"], "black", 80, W - 2 * X0), WHITE, A * pe)

def sc_points(im, d, t, D, A, sc):
    y = heading(d, t, A, sc, 250) + 40
    items = sc.get("items", [])[:4]; f = F("semi", 44)
    wrapped = [wrap(d, it, f, W - 2 * X0 - 180)[:3] for it in items]
    need = sum(70 + 56 * len(l) for l in wrapped); gap = clamp((1420 - y - need) / max(1, len(items) - 1), 28, 60)
    y += max(0, (1420 - y - need - gap * (len(items) - 1)) / 3)
    for i, lines in enumerate(wrapped):
        pr = ease_back(clamp((t - 0.8 - 0.4 * i) / 0.45)); pa = clamp((t - 0.8 - 0.4 * i) / 0.25)
        h = 70 + 56 * len(lines)
        if pa > 0:
            yy = y + 24 * (1 - pr)
            card(d, (X0, yy, W - X0, yy + h), A * pa, radius=28, border=CYAN if i == 0 else INDIGO, bw=2)
            text(d, (X0 + 36, yy + h / 2), f"{i + 1:02d}", F("black", 50), CYAN, A * pa, anchor="lm")
            for j, ln in enumerate(lines): text(d, (X0 + 140, yy + 35 + 56 * j), ln, f, WHITE, A * pa)
        y += h + gap

def sc_stat(im, d, t, D, A, sc):
    if sc.get("label"): text(d, (X0, 300), sc["label"].upper(), F("bold", 30), CYAN, A * ramp(t, 0, 0.4))
    pb = clamp((t - 0.3) / 0.6); k = ease_back(pb)
    if pb > 0:
        v = str(sc.get("value", "")); fsz = 300 if len(v) <= 4 else (220 if len(v) <= 6 else 160)
        fv = F("black", int(fsz * (0.8 + 0.2 * k)))
        glow_dot(im, X0 + tw(d, v, fv) / 2, 360 + fsz * 0.55, 150, CYAN, 0.16 * A * pb)
        grad_text(im, (X0, 360 + 30 * (1 - k)), v, fv, A * clamp(pb * 2))
    y = 720
    f = F("black", 64)
    for i, ln in enumerate(wrap(d, sc.get("caption", ""), f, W - 2 * X0)[:3]):
        p = ramp(t, 0.8 + 0.1 * i, 0.5); text(d, (X0, y + 20 * (1 - p)), ln, f, WHITE, A * p); y += 80
    if sc.get("body"):
        f = F("reg", 40); y += 30
        for i, ln in enumerate(wrap(d, sc["body"], f, W - 2 * X0)[:4]):
            p = ramp(t, 1.3 + 0.08 * i, 0.5); text(d, (X0, y + 16 * (1 - p)), ln, f, SOFT, A * p); y += 54

def sc_cta(im, d, t, D, A, sc):
    text(d, (X0, 380 + 26 * (1 - ramp(t, 0, 0.5))), sc.get("line1", ""), fit(d, sc.get("line1", ""), "black", 120, W - 2 * X0), CYAN, A * ramp(t, 0, 0.5))
    text(d, (X0, 520 + 26 * (1 - ramp(t, 0.15, 0.5))), sc.get("line2", ""), fit(d, sc.get("line2", ""), "black", 104, W - 2 * X0), WHITE, A * ramp(t, 0.15, 0.5))
    if sc.get("sub"):
        text(d, (X0, 680 + 20 * (1 - ramp(t, 0.5, 0.5))), sc["sub"], fit(d, sc["sub"], "semi", 50, W - 2 * X0), HAZE, A * ramp(t, 0.5, 0.5))
    pb = ease_back(clamp((t - 0.9) / 0.5)); pa = clamp((t - 0.9) / 0.3)
    if pa > 0:
        fb = F("bold", 46); lbl = sc.get("button", "Follow Nexlore AI"); bw = tw(d, lbl, fb) + 110; yb = 820 + 20 * (1 - pb)
        grad_rect(im, (X0, yb, X0 + bw, yb + 116), 58, A * pa)
        d.rounded_rectangle(s(X0 + 4, yb + 4, X0 + bw - 4, yb + 112), radius=54 * SS, fill=C(NIGHT, 0.82 * A * pa))
        text(d, (X0 + bw / 2, yb + 58), lbl, fb, WHITE, A * pa, anchor="mm")
    pl = ramp(t, 1.3, 0.6)
    if pl > 0:
        ic = icon(150); m = ic.split()[3].point(lambda v: int(v * A * pl)); yy = 1120 + 30 * (1 - pl)
        im.paste(ic, s(X0, yy), m)
        text(d, (X0 + ic.width / SS + 34, yy + 75), "Nexlore AI", F("black", 76), WHITE, A * pl, anchor="lm")
        text(d, (X0 + ic.width / SS + 36, yy + 140), "One AI idea. Explained daily.", F("semi", 34), CYAN, A * pl, anchor="lm")

SCENES = {"hook": (sc_hook, 4.0), "stack": (sc_stack, 4.2), "section": (sc_section, 7.0), "predict": (sc_predict, 7.0),
          "points": (sc_points, 6.0), "stat": (sc_stat, 5.0), "cta": (sc_cta, 4.5)}

# ------------------------------------------------------------------ timeline + render
LEAD, TAIL = 0.2, 0.45
def vo_dir(spec_path): return os.path.splitext(spec_path)[0] + "_vo"
def probe(p):
    return media.duration(p)
def timeline(spec, spec_path):
    durs = []
    for i, sc in enumerate(spec["scenes"], 1):
        base = sc.get("min_seconds", SCENES[sc["type"]][1]); p = os.path.join(vo_dir(spec_path), f"{i:02d}.mp3")
        durs.append(max(base, LEAD + probe(p) + TAIL) if os.path.exists(p) else base)
    return durs, [sum(durs[:i]) for i in range(len(durs))], sum(durs)

def frame(spec, k, t, D, g0, total):
    init(); sc = spec["scenes"][k]; T = g0 + t
    im, d = background(T)
    fin = ease(clamp(t / 0.35)) if g0 > 0 else 1.0
    fout = clamp((D - t) / 0.3) if g0 + D < total - 0.01 else 1.0
    SCENES[sc["type"]][0](im, d, t, D, min(fin, fout), sc)
    header(im, d, T, total, spec.get("tag"))
    return im.resize((W, H), Image.LANCZOS)

def _render(args):
    spec, k, D, g0, total, out = args
    tmp = out[:-4] + ".tmp.mp4"
    p = subprocess.Popen([media.require("ffmpeg"), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p", "-r", str(FPS), tmp],
                         stdin=subprocess.PIPE)
    for i in range(int(round(D * FPS))): p.stdin.write(frame(spec, k, i / FPS, D, g0, total).tobytes())
    p.stdin.close()
    if p.wait() != 0: raise SystemExit(f"ffmpeg failed on scene {k + 1}")
    os.replace(tmp, out); return k

def decode(p, sr=48000):
    raw = subprocess.run([media.require("ffmpeg"), "-loglevel", "error", "-i", p, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0

def mix_audio(spec, spec_path, offs, total, out, sr=48000):
    n = int(total * sr); voice = np.zeros(n, np.float32)
    for i, off in enumerate(offs, 1):
        p = os.path.join(vo_dir(spec_path), f"{i:02d}.mp3")
        if not os.path.exists(p): continue
        a = decode(p, sr); st = int((off + LEAD) * sr); voice[st:st + len(a)] += a[:max(0, n - st)]
    env = np.clip(np.convolve(np.abs(voice) > 0.01, np.ones(int(0.25 * sr)) / int(0.25 * sr), mode="same") * 3, 0, 1)
    m = np.zeros(n, np.float32); mus = spec.get("music")
    mp = mus if mus and os.path.isabs(mus) else os.path.join(music_dir(), mus or "")
    if mus and os.path.exists(mp):
        m = decode(mp, sr)
        if len(m) < n: m = np.tile(m, n // len(m) + 1)
        m = m[:n] / (np.sqrt(np.mean(m[:n] ** 2)) + 1e-9) * 0.10
        tt = np.arange(n) / sr; m *= np.minimum(1, tt / 0.8) * np.minimum(1, (total - tt) / 2.0) * (1.0 - 0.6 * env)
    v = voice / (np.max(np.abs(voice)) + 1e-9) * 0.9 if voice.any() else voice
    st = np.stack([v + m, v + m], 1)
    with wave.open(out, "w") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(st, -1, 1) * 32767).astype(np.int16).tobytes())

def validate(spec, path=None):
    """Render-time validation, in core.spec so it can be tested without drawing a frame."""
    for warning in spec_mod.validate_render(spec, path):
        print("!!", warning)

def run(mode, spec_path, out=None):
    """`mode` is "preview" or "render". Returns the path written.

    Every artifact — the scene intermediates, the cover frame — is written beside `out`,
    not beside the spec. The spec is an input and may be read-only: inside a container,
    in site-packages after a pip install, or in a shared examples folder. Writing next to
    it was how `creatoros render /app/examples/specs/kv-cache.json -o /workspace/out.mp4`
    failed with a permission error on a path the user never named.
    """
    spec_path = os.path.abspath(spec_path)
    spec = spec_mod.load(spec_path); validate(spec, spec_path)
    durs, offs, total = timeline(spec, spec_path)
    print("durations", [round(x, 2) for x in durs], "total", round(total, 2), flush=True)
    # Outputs hang off `out` when it is given, and off the spec only as a fallback.
    base = os.path.splitext(os.path.abspath(out))[0] if out else os.path.splitext(spec_path)[0]
    if mode == "preview":
        init(); n = len(durs); cols = 4; tw_, th_ = 360, 640
        sheet = Image.new("RGB", (cols * tw_, ((n + cols - 1) // cols) * th_))
        for k, (D, g0) in enumerate(zip(durs, offs)):
            sheet.paste(frame(spec, k, D * 0.92, D, g0, total).resize((tw_, th_)), ((k % cols) * tw_, (k // cols) * th_))
        out = out or (base + ".preview.jpg")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        sheet.save(out, quality=86); print("preview", out); return out
    out = out or (base + ".mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    work = base + "_scenes"; os.makedirs(work, exist_ok=True)
    jobs = [(spec, k, D, g0, total, os.path.join(work, f"{k + 1:02d}.mp4")) for k, (D, g0) in enumerate(zip(durs, offs))]
    with Pool(min(os.cpu_count() or 4, 8)) as pool:
        for k in pool.imap_unordered(_render, sorted(jobs, key=lambda j: -j[2])): print("scene", k + 1, "done", flush=True)
    with open(os.path.join(work, "list.txt"), "w") as f:
        for j in jobs: f.write(f"file '{os.path.basename(j[5])}'\n")
    subprocess.run([media.require("ffmpeg"), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "list.txt"), "-c", "copy",
                    os.path.join(work, "silent.mp4")], check=True)
    mix_audio(spec, spec_path, offs, total, os.path.join(work, "mix.wav"))
    subprocess.run([media.require("ffmpeg"), "-y", "-loglevel", "error", "-i", os.path.join(work, "silent.mp4"), "-i", os.path.join(work, "mix.wav"),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-shortest", "-movflags", "+faststart", out], check=True)
    init(); frame(spec, 0, durs[0] * 0.95, durs[0], 0, total).save(base + ".cover.jpg", quality=90)
    for f_ in os.listdir(work): os.remove(os.path.join(work, f_))
    os.rmdir(work)
    print("video", out, round(total, 2), "s")
    return out


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2 or argv[0] not in ("preview", "render"):
        raise SystemExit("usage: python -m creatoros_autopilot.render.engine preview|render SPEC.json [OUT]")
    return run(argv[0], argv[1], argv[2] if len(argv) > 2 else None)


if __name__ == "__main__":
    main()
