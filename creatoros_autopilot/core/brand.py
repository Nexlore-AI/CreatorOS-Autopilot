# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brand kits: a channel's identity, as data.

The renderer used to draw the string "Nexlore AI" into the header of every frame, open
`nexlore-icon-512.png` unconditionally at startup, and hold the palette and the font map as
module constants. Anyone else running this got a video with someone else's name on it, or a
crash at frame zero when the icon was missing.

A brand kit is a YAML file, and nothing in it is required: a kit with only a name renders a
wordmark instead of a logo, and a kit with no palette uses the default one. The failure mode
for a missing asset is a plainer video, never an exception — a logo that moved must not be
able to stop a day's run.

Colours resolve once into a `Palette`, because the engine reads these inside per-frame
loops and parsing "#0ED1D3" thirty times a second has no purpose.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from ..interfaces.brand import Brand, LanguageMix

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "brands"

#: The look a kit inherits when it says nothing. It is the current house style so that an
#: existing spec renders unchanged, but named generically — a default, not an identity.
DEFAULT_PALETTE = {
    "background": "#0B0A1C", "background_low": "#120E33",
    "text": "#FFFFFF", "muted": "#9AA0BC", "soft": "#C9CDE0",
    "accent": "#0ED1D3", "accent_2": "#298AB4", "accent_3": "#4C52B2", "accent_4": "#580CA3",
    "violet": "#AC86D1", "surface": "#16152F", "card": "#1F1D3F", "border": "#2E2B55",
    "glow_top": "#0B4F63", "glow_bottom": "#3A1478",
    "green": "#10B981", "amber": "#F5A524", "red": "#F0506E",
}

DEFAULT_FONTS = {
    "black": "InterDisplay-Black.otf", "bold": "Inter-Bold.otf",
    "semi": "Inter-SemiBold.otf", "reg": "Inter-Regular.otf",
    "mono": "DejaVuSansMono-Bold.ttf",
}


class BrandError(ValueError):
    pass


def parse_colour(value) -> tuple:
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise BrandError(f"{value!r} is not a colour: expected #RRGGBB")
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise BrandError(f"{value!r} is not a colour: expected #RRGGBB") from None


@dataclass
class Palette:
    colours: dict = field(default_factory=dict)

    def __getattr__(self, name):
        """Colours as attributes: `palette.accent` rather than `palette.colours["accent"]`.

        Reads `__dict__` directly rather than `self.colours`. `__getattr__` runs whenever
        normal lookup fails, and during unpickling an instance exists with an empty
        `__dict__` before its state is restored — so `self.colours` would itself miss,
        call `__getattr__` again, and recurse until the interpreter gave up. That happens
        in a render worker, where the only symptom is a job that never finishes.
        """
        colours = self.__dict__.get("colours")
        if colours is not None and name in colours:
            return colours[name]
        raise AttributeError(f"the palette has no colour {name!r}")

    @classmethod
    def build(cls, overrides: dict | None = None) -> "Palette":
        merged = {**DEFAULT_PALETTE, **(overrides or {})}
        unknown = set(merged) - set(DEFAULT_PALETTE)
        if unknown:
            raise BrandError(f"unknown palette colour(s) {sorted(unknown)}; "
                             f"valid: {sorted(DEFAULT_PALETTE)}")
        return cls({name: parse_colour(value) for name, value in merged.items()})

    @property
    def gradient(self) -> list:
        return [(0.0, self.accent), (0.35, self.accent_2),
                (0.68, self.accent_3), (1.0, self.accent_4)]

    @property
    def named(self) -> dict:
        """Colours a spec may ask for by name, e.g. a flow block's `color`."""
        return {"cyan": self.accent, "blue": self.accent_2, "indigo": self.accent_3,
                "violet": self.violet, "green": self.green, "amber": self.amber,
                "red": self.red}


@dataclass
class BrandKit:
    name: str = "CreatorOS"
    tagline: str = ""
    logo: Path | None = None
    cta: str = ""
    palette: Palette = field(default_factory=Palette.build)
    fonts: dict = field(default_factory=lambda: dict(DEFAULT_FONTS))
    language: LanguageMix = field(default_factory=LanguageMix)
    source: str = "built-in default"

    @property
    def cta_label(self) -> str:
        return self.cta or f"Follow {self.name}"

    def to_brand(self) -> Brand:
        return Brand(name=self.name, tagline=self.tagline, logo=str(self.logo or ""),
                     palette=dict(self.palette.colours), fonts=dict(self.fonts),
                     cta=self.cta, language=self.language)


def _language(data: dict | None) -> LanguageMix:
    if not data:
        return LanguageMix()
    known = {f.name for f in fields(LanguageMix)}
    unknown = set(data) - known
    if unknown:
        raise BrandError(f"unknown language key(s) {sorted(unknown)}; valid: {sorted(known)}")
    lexicon = data.get("lexicon") or ()
    if isinstance(lexicon, str):
        lexicon = tuple(w for w in lexicon.split() if w)
    ratio = data.get("ratio")
    if ratio is not None and not 0 <= float(ratio) <= 1:
        raise BrandError(f"language.ratio must be between 0 and 1, got {ratio}")
    return LanguageMix(primary=data.get("primary", "en"),
                       secondary=data.get("secondary", ""),
                       ratio=None if ratio is None else float(ratio),
                       tolerance=float(data.get("tolerance", 0.085)),
                       script=data.get("script", ""),
                       lexicon=tuple(lexicon))


def search_path(explicit=None) -> list:
    if explicit:
        return [Path(explicit).expanduser()]
    from ..runtime import user_config_dir
    return [Path.cwd() / "brands", user_config_dir() / "brands", BUILTIN_DIR]


def find(name: str, explicit=None):
    direct = Path(name).expanduser()
    if direct.suffix in (".yaml", ".yml") and direct.is_file():
        return direct
    for directory in search_path(explicit):
        for suffix in (".yaml", ".yml"):
            candidate = directory / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def available(explicit=None) -> list:
    found = {}
    for directory in search_path(explicit):
        if directory.is_dir():
            for path in sorted(directory.glob("*.y*ml")):
                found.setdefault(path.stem, path)
    return sorted(found)


def load(name: str = "", explicit=None) -> BrandKit:
    """Load a kit by name. An empty name is the built-in default, not an error."""
    if not name:
        return BrandKit()
    path = find(name, explicit)
    if path is None:
        raise BrandError(
            f"no brand kit named {name!r}. Available: "
            f"{', '.join(available(explicit)) or '(none)'}. A kit is a YAML file in "
            f"./brands/ or your config directory.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BrandError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise BrandError(f"{path}: a brand kit must be a mapping")

    known = {"name", "tagline", "logo", "cta", "palette", "fonts", "language"}
    unknown = set(data) - known
    if unknown:
        raise BrandError(f"{path}: unknown key(s) {sorted(unknown)}; valid: {sorted(known)}")

    logo = None
    if data.get("logo"):
        candidate = Path(str(data["logo"])).expanduser()
        if not candidate.is_absolute():
            candidate = (path.parent / candidate).resolve()
        if candidate.is_file():
            logo = candidate
        else:
            print(f"!! brand {name}: no logo at {candidate} — using the wordmark instead")

    fonts = {**DEFAULT_FONTS, **(data.get("fonts") or {})}
    unknown_fonts = set(fonts) - set(DEFAULT_FONTS)
    if unknown_fonts:
        raise BrandError(f"{path}: unknown font role(s) {sorted(unknown_fonts)}; "
                         f"valid: {sorted(DEFAULT_FONTS)}")
    try:
        palette = Palette.build(data.get("palette"))
    except BrandError as exc:
        raise BrandError(f"{path}: {exc}") from None

    return BrandKit(name=str(data.get("name") or name),
                    tagline=str(data.get("tagline") or ""), logo=logo,
                    cta=str(data.get("cta") or ""), palette=palette, fonts=fonts,
                    language=_language(data.get("language")), source=str(path))
