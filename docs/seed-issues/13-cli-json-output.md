---
title: Add `--json` to `creatoros doctor`, `creatoros status` and `creatoros brands`
labels: good first issue, enhancement
---

These three commands print a report for a human to read. Add a `--json` flag to each so the
same information comes out as one machine-readable object, and the CLI can be used from a
script or a CI job instead of being grepped.

## Why this matters

`creatoros doctor` is the command that tells you what is missing before a run. Today the
only way to act on its answer in a script is to parse aligned columns, so people either
re-implement the checks or skip them. The same is true of `creatoros status`, which is how
you find out whether today's four videos rendered and what is still unpublished — exactly
the thing a monitoring job wants to read, and exactly the thing that is currently only
readable by eye.

The data is already structured. `doctor.collect()` returns a `list[Check]`; `report()` only
renders it. This issue is mostly about not throwing that structure away.

## Where to start

- `creatoros_autopilot/cli/doctor.py` — the `Check` dataclass (`name`, `state`, `detail`,
  `fix`, `required`), the `OK` / `WARN` / `FAIL` constants, `collect(settings, source) ->
  list[Check]` and `report(checks) -> int`. `collect()` needs no change at all. Add a
  second renderer beside `report()` rather than rewriting it.
- `creatoros_autopilot/cli/main.py` — `cmd_doctor`, `cmd_status`, `cmd_brands`, and
  `build_parser()`, where the `doctor_p`, `status` and `brands_p` subparsers are created.
- What `cmd_status` already gathers, and where it comes from:
  `creatoros_autopilot/core/pipeline.py::rendered_videos(job_dir)`,
  `pipeline._job_id_for(video)`, `pipeline.STAMP`,
  `creatoros_autopilot/core/ledger.py::Ledger.done_platforms(job_id)`, and
  `creatoros_autopilot/core/schedule.py`'s `today()`, `is_due()` and `parse_times()`.
- `cmd_brands` uses `creatoros_autopilot/core/brand.py`'s `available()`, `load(name)` and
  `BrandError`.
- **Closest existing example:** `creatoros_autopilot/providers/publish/local_folder.py`.
  `_metadata()` builds a JSON document for a consumer and `_write()` dumps it with
  `json.dumps(payload, indent=2, ensure_ascii=False, default=str)`. Copy the shape of that,
  including `ensure_ascii=False` — narration and captions are Devanagari and `\uXXXX`
  escapes make the output unreadable for no gain.
- Tests live in `tests/test_cli.py` (`TestParser`, `TestCommands`, `TestErrors`).
- `README.md` has a Commands table at the "## Commands" heading; add the flag there.

## What done looks like

- [ ] `creatoros doctor --json` writes exactly one JSON object to stdout and nothing else.
- [ ] The exit code is unchanged: 0, or 1 when a *required* check failed, same as `report()`
      returns today. Also put the answer in the document (`"ok": false`, plus the names of
      the failed checks) so a caller does not have to re-derive it.
- [ ] `creatoros status --json` and `creatoros brands --json` do the same.
- [ ] `--json` is accepted *after* the subcommand name. With argparse that means adding it
      to each of the three subparsers, or defining it once on an
      `argparse.ArgumentParser(add_help=False)` and passing `parents=[...]`. A single
      top-level `--json` would only work as `creatoros --json doctor`, which nobody types.
- [ ] A test beside `tests/test_cli.py::TestCommands::test_doctor_never_prints_a_secret`
      that runs `main(["doctor", "--json"])` with the same two environment variables set and
      asserts the value, and the `sk-` / `zk-` prefixes, are absent from stdout.
- [ ] A test that `json.loads(capsys.readouterr().out)` succeeds for each of the three
      commands.
- [ ] The human output is byte-identical without the flag. The existing tests assert exact
      substrings (`"rendered     : 0"`, `"python"`, `"ffmpeg"`, `"tts plugins"`), so if you
      find yourself editing those assertions, you have changed something you did not mean to.

## Gotchas

**The version banner will break your JSON.** `cmd_doctor` prints
`f"CreatorOS Autopilot {__version__}\n"` before calling `report()`. One line of prose above
a JSON object makes `json.loads` fail on stdout, and `creatoros status` prints eight such
lines as it goes. Build the whole structure first and dump it once; send the banner to
stderr or drop it in JSON mode.

**The no-secrets guarantee has to hold in JSON too.** The existing test
(`test_doctor_never_prints_a_secret`) asserts over the whole of stdout, not over particular
fields, and it checks for the `sk-` and `zk-` prefixes as well as the value. `Check.detail`
and `Check.fix` are provider-authored strings — look at `_plugin()` in `doctor.py`, which
splices `ready.detail` and `ready.fix` straight in, and which also catches an exception from
`available()` and puts `f"{name}: {type(exc).__name__}: {exc}"` into the detail. That
exception text is not under doctor's control, so do not widen what JSON carries in the hope
of being more useful: no `os.environ`, no settings dump, no raw exception `repr`. `_secret()`
returns a bool on purpose and its docstring says why.

**Do not emit a success-shaped document on failure.** If the config is unparseable,
`settings_mod.load()` raises and `main()` prints `creatoros: <reason>` to stderr and returns
1, leaving stdout empty. That is the correct behaviour and it should stay that way. The
tempting "fix" is to always print something valid — `{}`, or `{"checks": []}` — and a CI job
that parses that, counts the failures and finds none then reports a healthy machine that
cannot even read its own config. Empty stdout plus a non-zero exit is a better answer than a
parseable lie. For the same reason, `--json` must not change the exit code: a scripted caller
is more likely to check `$?` than to read the body.

**`json.dumps` raises `TypeError` on a `Path` and on a `set`.** In `cmd_status`,
`settings.workspace_dir`, `job_dir` and the second element of each pair from
`rendered_videos()` are `Path`s, and `Ledger.done_platforms()` returns a `set[str]`. Convert
with `str()` and `sorted()` where you build the dict — `sorted()` also gives the platforms a
stable order, which matters when something diffs two days of output. `local_folder` passes
`default=str` as a backstop for arbitrary user data, which is worth copying but is not a
substitute for converting the values you already know about.

**Keep a broken brand kit visible.** `cmd_brands` currently prints `  x <name>: <error>` and
continues past a kit that fails to load. In JSON, give that entry an `error` field — do not
let it fall out of the list. A kit that silently disappears from the output is the "silent
empty list" failure this project keeps running into: the user sees four kits instead of five
and has no reason to suspect the fifth exists at all.
