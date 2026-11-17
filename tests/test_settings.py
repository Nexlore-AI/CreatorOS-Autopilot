# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config is data, secrets are not config, and a typo is an error."""
import os
from pathlib import Path

import pytest

from creatoros_autopilot.core import settings as S

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("CREATOROS_"):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


class TestDefaults:
    def test_nothing_publishes_by_default(self):
        """Principle 5. A tool that posts in your name must not do it until you say so."""
        settings = S.Settings()
        assert settings.publish.mode == "draft"
        assert settings.publish.platforms == []

    def test_no_provider_is_assumed(self):
        settings = S.Settings()
        assert settings.tts.backend == "none"
        assert settings.agent.provider == "claude_code"

    def test_defaults_load_with_no_config_file(self, cfg):
        assert S.load().timezone == "UTC"


class TestPrecedence:
    def test_file_beats_defaults(self, cfg):
        (cfg / "creatoros.yaml").write_text("timezone: Asia/Kolkata\npublish: {mode: auto}\n")
        settings = S.load()
        assert settings.timezone == "Asia/Kolkata"
        assert settings.publish.mode == "auto"

    def test_env_beats_file(self, cfg, monkeypatch):
        (cfg / "creatoros.yaml").write_text("publish: {mode: auto}\n")
        monkeypatch.setenv("CREATOROS_PUBLISH_MODE", "draft")
        assert S.load().publish.mode == "draft"

    def test_env_lists_are_split(self, cfg, monkeypatch):
        monkeypatch.setenv("CREATOROS_POST_TIMES", "07:00,08:00")
        monkeypatch.setenv("CREATOROS_PUBLISH_PLATFORMS", "instagram,youtube")
        settings = S.load()
        assert settings.publish.post_times == ["07:00", "08:00"]
        assert settings.publish.platforms == ["instagram", "youtube"]

    def test_env_ints_are_coerced(self, cfg, monkeypatch):
        monkeypatch.setenv("CREATOROS_VIDEOS_PER_DAY", "4")
        assert S.load().content.videos_per_day == 4


class TestMistakes:
    def test_an_unknown_key_is_an_error(self, cfg):
        """Silently ignoring `publsh:` would mean believing you set draft mode while the
        tool published. For something that posts in public that is not acceptable."""
        (cfg / "creatoros.yaml").write_text("publsh: {mode: auto}\n")
        with pytest.raises(ValueError, match="publsh"):
            S.load()

    def test_an_unknown_nested_key_is_an_error(self, cfg):
        (cfg / "creatoros.yaml").write_text("publish: {moed: auto}\n")
        with pytest.raises(ValueError, match="moed"):
            S.load()

    def test_broken_yaml_names_the_file(self, cfg):
        (cfg / "creatoros.yaml").write_text("publish: [unclosed\n")
        with pytest.raises(ValueError, match="creatoros.yaml"):
            S.load()

    def test_an_explicit_missing_config_is_an_error(self, cfg):
        with pytest.raises(FileNotFoundError):
            S.load(cfg / "nope.yaml")


class TestSecrets:
    def test_env_file_loads_into_the_environment_only(self, cfg, monkeypatch):
        (cfg / ".env").write_text("ELEVENLABS_API_KEY=secret-value\n# a comment\n\n")
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        names = S.load_env(cfg)
        assert names == ["ELEVENLABS_API_KEY"]
        assert os.environ["ELEVENLABS_API_KEY"] == "secret-value"

    def test_load_env_returns_names_never_values(self, cfg, monkeypatch):
        (cfg / ".env").write_text("ZERNIO_API_KEY=zk-do-not-print\n")
        monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
        assert "zk-do-not-print" not in repr(S.load_env(cfg))

    def test_the_real_environment_wins_over_the_file(self, cfg, monkeypatch):
        (cfg / ".env").write_text("ZERNIO_API_KEY=from-file\n")
        monkeypatch.setenv("ZERNIO_API_KEY", "from-shell")
        S.load_env(cfg)
        assert os.environ["ZERNIO_API_KEY"] == "from-shell"

    def test_quotes_and_export_prefixes_are_handled(self, cfg, monkeypatch):
        (cfg / ".env").write_text('export A="quoted"\nB=\'single\'\n')
        monkeypatch.delenv("A", raising=False)
        monkeypatch.delenv("B", raising=False)
        S.load_env(cfg)
        assert os.environ["A"] == "quoted" and os.environ["B"] == "single"

    def test_no_secret_reaches_the_settings_object(self, cfg, monkeypatch):
        (cfg / ".env").write_text("ELEVENLABS_API_KEY=secret-value\n")
        (cfg / "creatoros.yaml").write_text("tts: {backend: elevenlabs}\n")
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        assert "secret-value" not in repr(S.load())


class TestPaths:
    def test_every_path_hangs_off_the_workspace(self, cfg):
        (cfg / "creatoros.yaml").write_text(f"workspace: {cfg / 'ws'}\n")
        settings = S.load()
        for path in (settings.jobs_dir, settings.music_dir, settings.logs_dir,
                     settings.ledger_path, settings.lock_path, settings.job_dir("2026-01-01")):
            assert str(path).startswith(str(cfg / "ws"))

    @pytest.mark.parametrize("name", ["creatoros.example.yaml",
                                      "examples/nexlore-ai/creatoros.yaml"])
    def test_the_shipped_example_configs_are_committed_and_parse(self, name):
        """Resolved from this file, not the working directory.

        The relative-path version of this test passed locally and failed on all six CI
        jobs: an unanchored `creatoros.yaml` line in .gitignore had quietly excluded the
        Nexlore preset from the repo. A test that depends on where it is run from cannot
        tell "the file is missing" from "you are standing somewhere else".
        """
        path = REPO / name
        assert path.is_file(), f"{name} is missing from the repo — check .gitignore"
        assert S.load(path, use_env=False) is not None

    def test_the_example_config_publishes_nothing(self):
        """Every shipped preset but the Nexlore one defaults to not posting."""
        assert S.load(REPO / "creatoros.example.yaml", use_env=False).publish.mode == "draft"
