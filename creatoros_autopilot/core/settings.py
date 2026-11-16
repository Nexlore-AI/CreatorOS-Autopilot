# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings: one YAML file of data, one `.env` of secrets, nothing from my home folder.

What this replaces: `config.env` was a shell script, so only Bash could read it. Python
saw settings only as environment variables that Bash had already exported, and two of its
values were absolute paths into `$HOME/Developer/personal/NexloreAI/...`. A second user
could not configure the pipeline without editing the repo.

Rules this module enforces:

  * **Secrets never land here.** `.env` is read into `os.environ` and left there.
    A `Settings` object holds no API key, so logging or dumping one cannot leak a key.
  * **No path is ever built from `$HOME` in code.** Everything hangs off `workspace`,
    which comes from config, the `CREATOROS_WORKSPACE` environment variable, or the
    platform's own data directory.
  * **Precedence is explicit:** command-line argument > environment variable >
    `creatoros.yaml` > the defaults below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from ..runtime import default_workspace, user_config_dir

CONFIG_NAME = "creatoros.yaml"


# --------------------------------------------------------------------------- schema
@dataclass
class ContentSettings:
    videos_per_day: int = 1
    run_at: str = "06:30"


@dataclass
class AgentSettings:
    provider: str = "claude_code"
    max_turns: int = 240
    prompt: str = ""                 # path to an agent prompt; empty = the bundled one


@dataclass
class TTSSettings:
    backend: str = "none"            # none | elevenlabs | say   (piper lands in Phase 2)
    voice_id: str = ""
    model: str = "eleven_multilingual_v2"


@dataclass
class MusicSettings:
    mode: str = "library"            # library | generate | none
    dir: str = ""                    # empty = <workspace>/music


@dataclass
class YouTubeSettings:
    visibility: str = "public"       # public | unlisted | private
    category_id: str = "28"          # 28 = Science & Technology


@dataclass
class PublishSettings:
    # Safe by default (principle 5): nothing goes live unless the user asks for it.
    mode: str = "draft"              # draft | auto
    platforms: list[str] = field(default_factory=list)
    offset_days: int = 1
    post_times: list[str] = field(default_factory=lambda: ["09:00"])
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)


@dataclass
class Settings:
    workspace: str = ""              # empty = the platform's data directory
    timezone: str = "UTC"
    style: str = "nexlore-v3"
    content: ContentSettings = field(default_factory=ContentSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    publish: PublishSettings = field(default_factory=PublishSettings)

    # ---------------------------------------------------------------- derived paths
    @property
    def workspace_dir(self) -> Path:
        return Path(self.workspace).expanduser() if self.workspace else default_workspace()

    @property
    def music_dir(self) -> Path:
        return Path(self.music.dir).expanduser() if self.music.dir else self.workspace_dir / "music"

    @property
    def jobs_dir(self) -> Path:
        """One directory per run date, holding that day's specs, audio and MP4s."""
        return self.workspace_dir / "jobs"

    @property
    def ledger_path(self) -> Path:
        return self.workspace_dir / "ledger.jsonl"

    @property
    def history_path(self) -> Path:
        return self.workspace_dir / "history.jsonl"

    @property
    def logs_dir(self) -> Path:
        return self.workspace_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.workspace_dir / ".run.lock"

    def job_dir(self, date: str) -> Path:
        return self.jobs_dir / date

    def ensure_dirs(self) -> None:
        for path in (self.workspace_dir, self.jobs_dir, self.music_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- loading
def _build(cls, data: Any):
    """Construct a settings dataclass from plain YAML data, rejecting unknown keys.

    A typo in `creatoros.yaml` has to be an error. Silently ignoring `publsh:` would mean
    a user believing they set draft mode while the default was something else, and for a
    tool that posts in public that is not an acceptable failure mode.
    """
    if not is_dataclass(cls):
        return data
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping for {cls.__name__}, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(
            f"unknown setting(s) {sorted(unknown)} in {cls.__name__.replace('Settings','').lower() or 'top level'}; "
            f"valid keys: {sorted(known)}"
        )
    kwargs = {}
    for name, f in known.items():
        if name not in data:
            continue
        kwargs[name] = _build(f.type if is_dataclass(f.type) else _nested(cls, name), data[name])
    return cls(**kwargs)


_NESTED = {
    ("Settings", "content"): ContentSettings,
    ("Settings", "agent"): AgentSettings,
    ("Settings", "tts"): TTSSettings,
    ("Settings", "music"): MusicSettings,
    ("Settings", "publish"): PublishSettings,
    ("PublishSettings", "youtube"): YouTubeSettings,
}


def _nested(cls, name):
    """The dataclass for a nested key, or the raw value for a scalar.

    Resolved from a table rather than from `f.type`, because `from __future__ import
    annotations` turns every annotation into a string and `is_dataclass("TTSSettings")`
    is False.
    """
    return _NESTED.get((cls.__name__, name), object)


# Environment overrides. Only settings worth changing per-run are listed: a knob that is
# reachable two ways is a knob that can disagree with itself.
ENV_OVERRIDES = {
    "CREATOROS_WORKSPACE": ("workspace",),
    "CREATOROS_TIMEZONE": ("timezone",),
    "CREATOROS_STYLE": ("style",),
    "CREATOROS_VIDEOS_PER_DAY": ("content", "videos_per_day"),
    "CREATOROS_RUN_AT": ("content", "run_at"),
    "CREATOROS_AGENT_PROVIDER": ("agent", "provider"),
    "CREATOROS_AGENT_MAX_TURNS": ("agent", "max_turns"),
    "CREATOROS_TTS_BACKEND": ("tts", "backend"),
    "CREATOROS_TTS_VOICE_ID": ("tts", "voice_id"),
    "CREATOROS_TTS_MODEL": ("tts", "model"),
    "CREATOROS_MUSIC_MODE": ("music", "mode"),
    "CREATOROS_MUSIC_DIR": ("music", "dir"),
    "CREATOROS_PUBLISH_MODE": ("publish", "mode"),
    "CREATOROS_PUBLISH_PLATFORMS": ("publish", "platforms"),
    "CREATOROS_PUBLISH_OFFSET_DAYS": ("publish", "offset_days"),
    "CREATOROS_POST_TIMES": ("publish", "post_times"),
}


def config_search_path(explicit: str | os.PathLike | None = None) -> list[Path]:
    """Where `creatoros.yaml` is looked for, in order."""
    if explicit:
        return [Path(explicit).expanduser()]
    found = []
    if os.environ.get("CREATOROS_CONFIG"):
        found.append(Path(os.environ["CREATOROS_CONFIG"]).expanduser())
    found.append(Path.cwd() / CONFIG_NAME)
    found.append(user_config_dir() / CONFIG_NAME)
    return found


def find_config(explicit: str | os.PathLike | None = None) -> Path | None:
    for candidate in config_search_path(explicit):
        if candidate.is_file():
            return candidate
    return None


def load_env(workdir: str | os.PathLike | None = None) -> list[str]:
    """Read `.env` into `os.environ` and return the names it set.

    Names only: the values are secrets and must not be returned, logged or echoed. An
    existing environment variable wins, so `KEY=x creatoros run` beats the file.
    """
    path = Path(workdir or Path.cwd()) / ".env"
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _coerce(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def apply_env(settings: Settings) -> Settings:
    for env_name, path in ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        target = settings
        for part in path[:-1]:
            target = getattr(target, part)
        leaf = path[-1]
        setattr(target, leaf, _coerce(getattr(target, leaf), raw))
    return settings


def load(config: str | os.PathLike | None = None, *, use_env: bool = True) -> Settings:
    """The settings for this run, plus `.env` loaded into the environment."""
    if use_env:
        load_env()
    path = find_config(config)
    if path is None:
        if config:
            raise FileNotFoundError(f"no config file at {config}")
        settings = Settings()
    else:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} is not valid YAML: {exc}") from None
        try:
            settings = _build(Settings, data)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from None
        settings_path = path
        settings.__dict__["_source"] = str(settings_path)
    return apply_env(settings) if use_env else settings


def source_of(settings: Settings) -> str:
    return settings.__dict__.get("_source", "built-in defaults")
