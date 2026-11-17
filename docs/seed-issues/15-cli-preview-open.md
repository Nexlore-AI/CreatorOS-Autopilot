---
title: `creatoros preview --open`, and a clickable path from `creatoros render`
labels: good first issue, enhancement
---

Add an `--open` flag to `creatoros preview` that opens the contact sheet in the default
image viewer, and make `creatoros render` print its output path as a `file://` URL that most
terminals turn into a link.

## Why this matters

The contact sheet is the cheap check before an expensive render — one still per scene, so
you can see that a hook line did not overflow before you spend minutes encoding video. That
only works if looking at it is cheap too, and right now the command prints a path and you go
and find the file. Same for the finished MP4. It is a small change that removes friction from
the loop people use most.

## Where to start

- `creatoros_autopilot/cli/main.py` — `cmd_preview` calls `style.preview(...)` and discards
  the `Path` it returns; `cmd_render` does the same with `style.render(...)`. Those return
  values are what you need.
- `creatoros_autopilot/providers/style/nexlore_v3.py` — `render()` and `preview()` both
  return `Path(engine.run(...))`, which is the file that was actually written. Note that it
  is only absolute when `-o` was not given: `engine.run` derives the default output from
  `os.path.abspath(spec_path)`, but an explicit `out` is passed through exactly as the user
  typed it. See the `as_uri()` gotcha below.
- `creatoros_autopilot/render/engine.py` already prints `preview <path>` and
  `video <path> <n> s`. Leave it alone. It is style-specific, and `CONTRIBUTING.md` singles
  that file out: its output is verified pixel-for-pixel and a diff through it is unreadable.
  Printing the URL in `cmd_render` / `cmd_preview` also means a third-party style gets it for
  free.
- **Closest existing example for the URL:**
  `creatoros_autopilot/providers/publish/local_folder.py::_uri` is one line —
  `Path(os.path.abspath(path)).as_uri()` — and its docstring says why it is built by pathlib
  rather than by string concatenation. Reuse that approach.
- **Closest existing example for the opening:** `creatoros_autopilot/runtime/__init__.py::
  notify()`. It is the same kind of operation — a best-effort handoff to the desktop — and it
  sets the contract to copy: branch on `is_macos()` / `is_windows()`, run the command with a
  timeout, catch everything, return a bool, never raise, never block. Your new function
  belongs next to it, because `runtime/` is the only directory allowed to know which OS it is
  on.
- Tests: `tests/test_cli.py` (`TestCommands`) and `tests/test_portability.py`.

## What done looks like

- [ ] `creatoros preview SPEC --open` writes the contact sheet and opens it; without the flag
      the behaviour is byte-identical to today.
- [ ] `creatoros render SPEC` prints the output as a `file://` URL built with
      `Path.as_uri()`.
- [ ] The opening is one function in `creatoros_autopilot/runtime/__init__.py` (something
      like `open_path(path) -> bool`) and nothing outside `runtime/` names an OS.
- [ ] `open` and `xdg-open` are added to `OS_SPECIFIC_BINARIES` in
      `tests/test_portability.py`. The set today is `osascript, launchctl, schtasks,
      systemctl, notify-send, powershell, brew, say, date` — the file openers are not in it,
      so as things stand nothing would stop someone writing the call in `cli/` later. Adding
      them is what turns this rule from a convention into the enforced kind.
- [ ] A test that the opener is not called when the preview raised.
- [ ] Every test monkeypatches the opener. CI runs headless on three OSes; a test that really
      launches an image viewer either hangs a runner or leaves a process behind.
- [ ] A failed open (no viewer, no `DISPLAY`) prints one line saying so and still exits 0 when
      the preview itself succeeded.
- [ ] The flag has a help string, so it shows up in `creatoros preview --help`.

## Gotchas

**Never open a path that a failed render left behind.** The preview path is deterministic —
`engine.run` writes `<spec-stem>.preview.jpg` unless `-o` overrides it — so it is very easy
to compute the path, open it, and show the user the contact sheet from an earlier run after
this one failed. They then conclude their change had no effect. Open only the `Path` the call
returned, after it returned. This is the same shape as the near-silent narration clip that
shipped inside a video: the file exists, so nothing looks wrong.

**`start` is not a program.** On Windows it is a `cmd.exe` builtin, so
`subprocess.run(["start", path])` raises `FileNotFoundError`, and
`tests/test_portability.py::test_no_shell_true_subprocesses` bans the `shell=True` escape
hatch. Use `os.startfile(path)`. It only exists on Windows, so put it inside an
`if sys.platform == "win32":` branch — mypy narrows on that comparison and will otherwise
report `Module has no attribute "startfile"`, and `runtime/` is not exempt from mypy (the
`[[tool.mypy.overrides]]` block in `pyproject.toml` only ignores `render.*` and
`providers.*`). If you use `cmd /c start` instead, the empty title argument is required —
`cmd /c start "" "<path>"` — or a quoted path is taken as the window title and nothing opens.

**`as_uri()` raises `ValueError` on a relative path.** `creatoros render spec.json -o out.mp4`
gives you a relative `out`, and `cmd_render` passes it straight through. Make it absolute
first, as `local_folder._uri` does. Do not percent-encode by hand: `as_uri()` is there
because it handles a space and a Windows drive letter correctly.

**A zero exit from the opener does not mean a human saw the image.** `open` and `xdg-open`
return as soon as the request is handed to the desktop, and `os.startfile` returns
immediately. So do not print "opened" on success — print the URL unconditionally, and add one
line with the reason only when the handoff itself failed. Pass a timeout the way `notify()`
does (it uses `timeout=20`): depending on the handler, `xdg-open` can block for as long as
the viewer it launched stays open, and a render that already succeeded must not be held up by
a GUI.

**Keep it out of the pipeline.** `core/pipeline.py` runs unattended under launchd, systemd or
a Scheduled Task, where there is no desktop to open anything on and nobody to look at it.
`--open` is a flag on one interactive command; nothing in `core/` should learn about it.
