# Nexlore AI: upload already-rendered reels

Upload the reels in `out/DATE/`. Don't research or re-render anything.

1. Read `config.env` for `UPLOAD_BACKEND`, `UPLOAD_MODE` and `POST_TIMES`.
   If `UPLOAD_MODE_OVERRIDE` is given at the end of this prompt, use it instead of `UPLOAD_MODE`.
2. Work on every `*.mp4` in `out/DATE/` whose `history.jsonl` entry is missing or has `"ok": false`.
   The caption for each reel is in its matching `*.caption.txt` file.

## If `UPLOAD_BACKEND=zernio`

Use the `./zernio` wrapper (it loads `config.env` + `.env` itself). Don't call the API by hand.

- First run `./zernio accounts`. If it reports no connected accounts, stop and say so:
  the accounts must be connected at https://zernio.com before anything can post.
- Then, per reel, map `UPLOAD_MODE` to a `--mode`:
  - `project`  -> `./zernio post FILE.mp4 --mode draft` (a Zernio draft; nothing goes out)
  - `schedule` -> `./zernio post FILE.mp4 --mode schedule` (uses the `POST_TIMES` slot for its `0N_` prefix, in `POST_TZ`)
  - `publish`  -> `./zernio post FILE.mp4 --mode publish` (goes out immediately)
- `./zernio post` appends its own `history.jsonl` line, so don't duplicate it; still update `STATUS.md`.
- To preview the exact payload without uploading, add `--dry-run`.

## If `UPLOAD_BACKEND=metapilot` (default)

Upload through the `metapilot-native` MCP server.

- List the `metapilot-native` tools and read their descriptions.
- `project`: upload the MP4 + caption into the connected Edit app project as a draft. Don't publish.
- `schedule`: schedule it at the `POST_TIMES` slot that matches its `0N_` prefix (IST).
- `publish`: publish it.
- If the server can't do that mode, fall back to `project`.
- Append or update one line per reel in `history.jsonl`
  (`{"date","slot","title","file","upload":{"mode","id_or_url","ok"}}`).

## Both backends

3. Update `out/DATE/STATUS.md` with what was uploaded and any id or URL returned.
4. Finish with one line per reel: file → result.
