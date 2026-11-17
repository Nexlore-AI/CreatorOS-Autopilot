# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command surface: it runs, it reports, and it fails with a sentence not a traceback."""
import pytest

from creatoros_autopilot.cli.main import build_parser, main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "creatoros.yaml").write_text(
        f"workspace: {tmp_path / 'ws'}\ntimezone: Asia/Kolkata\n")
    return tmp_path


class TestParser:
    def test_every_documented_command_exists(self):
        parser = build_parser()
        commands = next(a for a in parser._actions if a.dest == "command").choices
        assert set(commands) >= {"render", "preview", "audio", "publish", "run",
                                 "status", "doctor", "schedule"}

    def test_a_command_is_required(self, capsys):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_publish_defaults_to_draft(self):
        assert build_parser().parse_args(["publish", "x.mp4"]).mode == "draft"


class TestCommands:
    def test_doctor_runs_and_reports(self, workspace, capsys):
        code = main(["doctor"])
        out = capsys.readouterr().out
        assert code in (0, 1)
        assert "python" in out and "ffmpeg" in out

    def test_doctor_never_prints_a_secret(self, workspace, monkeypatch, capsys):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-do-not-print-me")
        monkeypatch.setenv("ZERNIO_API_KEY", "zk-do-not-print-me")
        main(["doctor"])
        out = capsys.readouterr().out
        assert "do-not-print-me" not in out
        assert "ELEVENLABS_API_KEY" in out

    def test_status_reports_an_empty_day(self, workspace, capsys):
        assert main(["status", "--date", "2026-11-17"]) == 0
        out = capsys.readouterr().out
        assert "rendered     : 0" in out

    def test_schedule_show_writes_nothing(self, workspace, capsys):
        before = set(workspace.rglob("*"))
        assert main(["schedule", "show"]) == 0
        assert "backend:" in capsys.readouterr().out
        assert set(workspace.rglob("*")) == before, "show must not touch the system"

    def test_run_with_no_platforms_renders_nothing_and_says_so(self, workspace, capsys):
        assert main(["run", "--date", "2026-11-17", "--skip-agent"]) == 0
        assert "no rendered videos" in capsys.readouterr().out


class TestErrors:
    def test_a_bad_config_key_is_one_line_not_a_traceback(self, workspace, capsys):
        (workspace / "creatoros.yaml").write_text("publsh: {mode: auto}\n")
        assert main(["status"]) == 1
        assert "creatoros: " in capsys.readouterr().err

    def test_a_missing_spec_is_one_line(self, workspace, capsys):
        assert main(["render", "nope.json"]) == 1
        assert "creatoros: " in capsys.readouterr().err

    def test_publish_without_platforms_explains_how_to_set_them(self, workspace, capsys):
        assert main(["publish", "x.mp4"]) == 1
        assert "publish.platforms" in capsys.readouterr().err

    def test_workspace_flag_overrides_the_config(self, workspace, capsys):
        main(["--workspace", str(workspace / "other"), "status"])
        assert str(workspace / "other") in capsys.readouterr().out
