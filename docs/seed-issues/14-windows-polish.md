---
title: Windows polish — run the first-run path on a real machine and fix what breaks
labels: help wanted, windows
---

CI runs the test suite, `creatoros doctor` and a render on `windows-latest`, so the code
imports and the renderer works there. Nobody has sat at a Windows machine and used it:
installed the daily job, waited for it to fire, read the output. This issue is that session,
plus a fix for each thing it finds.

This is not a good first issue. It needs a Windows machine (a VM is fine) and the patience
to wait for a scheduled task, and the work is "find out, then fix" rather than a known
change. It is labelled `help wanted` for that reason. The individual fixes it turns up are
likely to be small; please send them as separate PRs.

## Why this matters

Running on any OS is the thing this project claims that the shell scripts it replaced could
not do, and scheduling is the whole point of the word "autopilot". A Windows user who
installs the daily job and gets nothing the next morning has no way to find out why: on
macOS the launchd unit writes `~/Library/Logs/creatoros/run.{out,err}.log`, on Linux the
systemd unit lands in the journal, and the Windows shim redirects its output nowhere at all.

## Where to start

- `creatoros_autopilot/runtime/units.py` — `build()` dispatches on `os_name()`;
  `_schtasks(argv, hour, minute, env)` generates the `.cmd` shim at
  `%LOCALAPPDATA%\creatoros\scheduled-task.cmd` and the single
  `schtasks /Create /F /TN creatoros-autopilot /SC DAILY /ST HH:MM /TR "<path>"` command;
  `install(unit)` writes the file and runs `unit.install_cmds`; `remove(unit)` undoes it;
  `Unit.inspect` is the `schtasks /Query /TN creatoros-autopilot` line printed at the end.
- `creatoros_autopilot/runtime/__init__.py` — `notify()` (the PowerShell `NotifyIcon`
  balloon tip), `user_config_dir()` (`%APPDATA%\creatoros`) and `default_workspace()`
  (`%LOCALAPPDATA%\creatoros`).
- `creatoros_autopilot/cli/main.py` — `cmd_schedule`, with its `show` / `install` / `remove`
  actions.
- `creatoros_autopilot/cli/doctor.py` — `_config()`, whose timezone check hands back
  `"pip install tzdata"` as the fix when `os_name() == "windows"`.
- `pyproject.toml` — `"tzdata>=2024.1; sys_platform == 'win32'"`, with the comment saying
  why. The marker is what you are verifying actually fires on a clean install.
- `.github/workflows/ci.yml` — the Windows matrix leg, and in particular that its
  "First-run path" step runs under `shell: bash`. That is git bash, not `cmd.exe` or
  PowerShell, which is why console and quoting breakage is invisible to CI today.
- There is no test for `runtime/units.py`. `tests/test_schedule.py` is about
  `core/schedule.py`, and the only thing that touches the unit generator is
  `tests/test_cli.py::TestCommands::test_schedule_show_writes_nothing`. Anything you fix can
  take the first one.

**Run `creatoros schedule show` first.** It prints the generated unit, the path it would be
written to and the commands that would be run, and writes nothing — the test above asserts
that it touches no files. Read it before you install anything.

## What done looks like

- [ ] `creatoros schedule install` creates a task that actually fires at the configured
      time. Paste the `schtasks /Query /TN creatoros-autopilot /V /FO LIST` output into the
      PR.
- [ ] A failed `schtasks /Create` is reported as a failure rather than as `ran ...`. See the
      first gotcha; this is a confirmed bug and the single most valuable fix in this issue.
- [ ] A scheduled run leaves output somewhere a user can find, as it does on the other two
      platforms.
- [ ] `creatoros run` under the scheduled task survives Devanagari in its output.
- [ ] `pip install -e ".[dev]"` in a fresh venv pulls `tzdata`, and `creatoros doctor`
      reports `timezone ok` for a config with `timezone: Asia/Kolkata`.
- [ ] `notify()` either shows something on Windows 11, or it is changed or dropped with a
      note in the code saying what was tried.
- [ ] `creatoros init` without `--yes` (so the prompts are exercised), then `doctor`,
      `render` and `preview`, in both `cmd.exe` and PowerShell.
- [ ] `creatoros schedule remove` leaves no task and no `.cmd` behind.
- [ ] Each fix has a test, or the PR says why it cannot have one — `CONTRIBUTING.md` allows
      the second, and "it needs a real Scheduled Task" is a legitimate reason.

## Gotchas

**`units.install()` currently reports success for a failed Windows install.** The loop is:

```python
if done.returncode != 0 and cmd is not unit.install_cmds[0]:
    out.append(f"!! {' '.join(cmd)} -> ...")
else:
    out.append(f"ran {' '.join(cmd)}")
```

The exemption exists because on macOS and Linux the first command is a best-effort teardown
of a previous install, where a non-zero exit is normal. On Windows `install_cmds` has exactly
one element, and it is the real `schtasks /Create`. So access denied, a task name the policy
rejects, or a malformed `/ST` all end up printed as `ran schtasks /Create ...`, followed by
`check it with: schtasks /Query ...`, and the user believes the job is installed. This is the
exact shape of bug `CONTRIBUTING.md` is about. Fix it by marking which commands are teardown
instead of inferring it from position.

**`notify()` swallows every failure and nobody reads its answer.** It returns a bool and
never raises, which is right for a notification. But `core/pipeline.py::_announce` discards
the return value, and the docstring's claim that "`creatoros doctor` reports" it is not true
— nothing in `cli/doctor.py` calls `notify()`. So on Windows a notification that never
appears is indistinguishable from one that worked. Decide which it is, then either surface
the result or stop claiming it. Two specific things to check in the PowerShell snippet: it
loads only `System.Windows.Forms` but then uses `[System.Drawing.SystemIcons]::Information`,
so check whether `System.Drawing` resolves in a clean `powershell -NoProfile -NonInteractive`
session; and `ShowBalloonTip(5000)` is followed by `Start-Sleep -Seconds 1` and process exit,
which disposes the `NotifyIcon`, so check whether the toast survives that. Notification
settings or Focus Assist suppressing it is a legitimate "no" — the point is that the user is
told.

**It is console encoding, not file encoding.** Every file write in the project already
passes `encoding="utf-8"` (`core/ledger.py`, the manifest in `core/narrate.py`, the sidecar
in `providers/publish/local_folder.py`), so files are fine. Stdout is not: on Windows, Python
encodes to the ANSI code page when stdout is a pipe or a redirect rather than a console, so
one Devanagari character raises `UnicodeEncodeError` and kills a run that had already
rendered. `creatoros_autopilot/quality/langmix.py` prints Devanagari literals directly
(`क्योंकि, यही, समझो`) along with scene lines, and the langmix gate's messages are of the form
`'<word>' → say 'use'` — the `→` alone is unencodable in cp1252. Reproduce it with a redirect
(`creatoros audio spec.json > out.log` in `cmd.exe`), not in the console window. If you add a
log redirect to the `.cmd` shim, set `PYTHONUTF8=1` in it as well.

**`schtasks` quoting, with no shell to clean up after it.** `_schtasks()` passes `/TR` the
value `f'"{path}"'`: literal quote characters inside a single argv element, and
`tests/test_portability.py::test_no_shell_true_subprocesses` bans `shell=True`, so nothing
strips them. Check what actually lands in the task's action with `/V /FO LIST`, and check a
workspace path containing a space. The shim body writes `set KEY=VALUE` with the value
unescaped, so check a path containing `&` or `^` too.

**Check whether the task runs when nobody is logged in.** `schtasks /Create` is called with
no `/RU`, `/RP` or `/IT`; the "Logon Mode" row of `/V /FO LIST` tells you what you got. If it
needs an interactive session, `creatoros schedule install` should say so in its output rather
than letting the user find out from a day with no videos.

**Two smaller things in `install()`.** The shim is written into `%LOCALAPPDATA%\creatoros`,
which is also `default_workspace()` on Windows — check that nothing in a run cleans that
directory out from under it. And `os.chmod(unit.path, 0o755)` is close to a no-op on Windows
(it only toggles the read-only bit); it is not what makes a `.cmd` runnable, so do not read
it as evidence that the execute path was thought through.

**Report before you fix everything.** `CONTRIBUTING.md` will send back one large "Windows
fixes" commit. A comment on this issue listing what broke, then a PR per fix, is reviewable
and gets merged.
