# SPDX-License-Identifier: AGPL-3.0-or-later
"""`creatoros init`: the first five minutes, and the two rules it must not bend."""
import io
import os
import stat

import pytest

from creatoros_autopilot.cli import init_wizard as wizard
from creatoros_autopilot.core import settings as settings_mod


def run(directory, **kw):
    buffer = io.StringIO()
    code = wizard.run(wizard.Console(assume=True, stream=buffer), directory, **kw)
    return code, buffer.getvalue()


class TestFirstRun:
    def test_pressing_return_throughout_gives_a_working_config(self, tmp_path):
        code, _ = run(tmp_path)
        assert code == 0
        settings = settings_mod.load(tmp_path / "creatoros.yaml", use_env=False)
        assert settings.timezone and settings.style

    def test_the_default_setup_needs_no_key_and_posts_nothing(self, tmp_path):
        run(tmp_path)
        settings = settings_mod.load(tmp_path / "creatoros.yaml", use_env=False)
        assert settings.publish.platforms == []
        assert settings.publish.mode == "draft"
        assert settings.tts.backend == "none"
        assert settings.agent.provider == "none"

    def test_it_says_what_to_do_next(self, tmp_path):
        _, out = run(tmp_path)
        assert "creatoros doctor" in out and "creatoros render" in out

    def test_it_says_nothing_will_be_published(self, tmp_path):
        _, out = run(tmp_path)
        assert "Nothing will be published" in out

    def test_the_config_it_writes_actually_loads(self, tmp_path):
        """A wizard that writes a file the loader rejects is worse than no wizard, so it
        parses its own output before claiming success."""
        run(tmp_path)
        assert settings_mod.load(tmp_path / "creatoros.yaml", use_env=False)


class TestItDoesNotDestroyThings:
    def test_an_existing_config_is_not_replaced_without_asking(self, tmp_path):
        original = "workspace: /keep/me\n"
        (tmp_path / "creatoros.yaml").write_text(original)
        code, out = run(tmp_path)
        assert code == 1
        assert (tmp_path / "creatoros.yaml").read_text() == original
        assert "Nothing was changed" in out

    def test_force_replaces_it(self, tmp_path):
        (tmp_path / "creatoros.yaml").write_text("workspace: /old\n")
        code, _ = run(tmp_path, force=True)
        assert code == 0 and "/old" not in (tmp_path / "creatoros.yaml").read_text()

    def test_existing_env_entries_survive(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEEP_ME=yes\nELEVENLABS_API_KEY=old\n")
        wizard.write_env(env, {"ZERNIO_API_KEY": "new"})
        body = env.read_text()
        assert "KEEP_ME=yes" in body
        assert "ELEVENLABS_API_KEY=old" in body
        assert "ZERNIO_API_KEY=new" in body


class TestSecrets:
    def test_secrets_never_reach_the_yaml(self, tmp_path):
        answers = wizard.Answers(secrets={"ZERNIO_API_KEY": "zk-must-not-appear"})
        assert "zk-must-not-appear" not in wizard.to_yaml(answers)

    def test_secrets_are_never_echoed(self, tmp_path):
        env = tmp_path / ".env"
        written = wizard.write_env(env, {"ZERNIO_API_KEY": "zk-must-not-appear"})
        assert written == ["ZERNIO_API_KEY"]
        assert "zk-must-not-appear" not in repr(written)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_the_env_file_is_not_world_readable(self, tmp_path):
        env = tmp_path / ".env"
        wizard.write_env(env, {"ZERNIO_API_KEY": "x"})
        assert stat.S_IMODE(env.stat().st_mode) == 0o600

    def test_the_env_file_says_what_it_is_for(self, tmp_path):
        env = tmp_path / ".env"
        wizard.write_env(env, {"ZERNIO_API_KEY": "x"})
        assert "Secrets only" in env.read_text()
