# Security

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/Nexlore-AI/CreatorOS-Autopilot/security/advisories/new)
rather than as a public issue.

This is a one-maintainer project, so expect a first response within a week. If something is
actively being exploited, say so in the title.

## What is in scope

CreatorOS runs on a user's own machine with the user's own keys, so the sharpest risks are
about credentials and about posting things the user did not intend:

- **A key reaching anywhere but `.env` or the process environment** — a log line, an error
  message, a config file, a rendered frame, a metadata sidecar, a crash report.
- **Publishing something the user did not ask for** — a video posted when the mode is
  `draft`, a video posted twice, a private video made public, a disclosure flag dropped.
- **Code execution from untrusted input** — a spec file, a brand kit, an RSS feed or an API
  response causing a command to run.
- **A plugin escaping the interface**, or the registry loading something it should not.

## What is not

- A provider's own vulnerability. Report those to the provider; tell us too if CreatorOS
  makes it worse or could mitigate it.
- A key you committed yourself. Rotate it. `.gitignore` covers `.env`, a pre-commit hook
  rejects key-shaped values in config files, and CI runs gitleaks over the full history —
  but none of that can help a key pasted into a file we do not know about.
- Rate limits, quotas and platform policy. Those are account matters.

## What the project does about this already

| | |
|---|---|
| Secrets only ever in `.env` and the environment | no `Settings` object holds a key, so dumping settings cannot leak one |
| `.env` written `0600` by `creatoros init` | and never echoed back |
| `creatoros doctor` | reports a key as present or missing by name — never its value |
| Pre-commit | rejects key-shaped values in any committed config file |
| CI | gitleaks over the full history on every push |
| Publishing | `draft` by default; nothing goes live until you change it |
| Idempotency | an append-only ledger keyed on `(job id, platform)` |
| AI disclosure | set on every upload where the API supports it, and warned about loudly where it does not |

## Supported versions

Pre-1.0: only the latest release. Tell us anyway if you find something in an older one.
