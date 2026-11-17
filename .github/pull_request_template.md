## What changed

<!-- One or two sentences. What does this do that the repo could not do before? -->

## Why

<!-- The problem, not the patch. If it fixes a bug, what went wrong and what did it cost? -->

## How to test it

```bash
# The commands a reviewer should run.
```

## Checklist

- [ ] `pytest -q` passes
- [ ] `ruff check .` and `mypy` pass
- [ ] No secret is in a committed file — keys go in `.env`, which is gitignored
- [ ] A new provider is a new file plus an entry point, with nothing added to `core/`
- [ ] New behaviour has a test; a bug fix has a test that fails without the fix
- [ ] I have read [CLA.md](../CLA.md) and agree to it
