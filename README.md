# Codex sessions

<!-- TOC -->

- [Codex sessions](#codex-sessions)
  - [Install](#install)
  - [Usage](#usage)
  - [Codex Skill](#codex-skill)
  - [Markdown Detail](#markdown-detail)
  - [Notes](#notes)
  - [License](#license)
  - [Development](#development)

<!-- /TOC -->

Inspect, search, repair, import/export, and convert Codex session files.

It can turn session rollout files from a Codex home directory such as
`~/.codex/sessions/YYYY/MM/DD/rollout-<...>.jsonl` into readable YAML or dialogue-oriented Markdown:

```md
# User:

<...>

---

# Codex:

<...>
```

## Install

Install the latest version from GitHub:

```bash
pipx install git+https://github.com/alexchexes/codex-sessions.git
```

Or install from a local checkout:

```bash
git clone https://github.com/alexchexes/codex-sessions.git
cd codex-sessions

# If `pipx` is not installed yet, install it first:
# python -m pip install --user pipx
# python -m pipx ensurepath

pipx install .
```

Or run it from a checkout without installing:

```bash
PYTHONPATH=src python -m codex_sessions --help
```

In PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m codex_sessions --help
```

## Usage

The installed command is `codex-sessions`.

Convert to YAML:

```bash
codex-sessions sessions/YYYY/MM/DD/rollout.jsonl -o rollout.yaml
```

Use YAML explicitly:

```bash
codex-sessions --yaml sessions/YYYY/MM/DD/rollout.jsonl
```

Convert to Markdown:

```bash
codex-sessions sessions/YYYY/MM/DD/rollout.jsonl -o rollout.md
```

Use Markdown explicitly when no `.md` output path is supplied:

```bash
codex-sessions --md sessions/YYYY/MM/DD/rollout.jsonl
```

The longer `--format md` form is also supported.

Markdown output truncates base64 data images by default so large screenshots do
not fill the `.md` file with inline image payloads. The placeholder includes
the original rollout path/line and a short base64 prefix so the image can still
be found in the source JSONL. To write those images as real files next to the
Markdown and link them from the document, use:

```bash
codex-sessions --md --md-images extract sessions/YYYY/MM/DD/rollout.jsonl
```

Extracted images are written to a sibling `<markdown-stem>_assets/` directory.
Use `--md-images inline` only when you want to keep base64 image data inline in
the Markdown; inline images include a hidden comment pointing back to truncation
or extraction mode for future cleanup.

When no output path is supplied, the tool writes under the Codex home
directory, not the current directory. Codex home defaults to `CODEX_HOME` or
`~/.codex`, so a session rollout normally writes to a path like
`~/.codex/tmp/sessions/YYYY/MM/DD/rollout-<...>.yaml`.

Convert by session ID:

```bash
codex-sessions 019dd5ce-19e1-78c3-9313-325228ddd983
```

Write the converted session to the current directory:

```bash
codex-sessions 019dd5ce-19e1-78c3-9313-325228ddd983 -o ./
```

Use a specific Codex home directory for session ID lookup and default output:

```bash
codex-sessions --codex-home ~/.codex 019dd5ce-19e1-78c3-9313-325228ddd983
```

List Codex sessions from `CODEX_HOME` or `~/.codex` and cross-check
`session_index.jsonl` against actual session files:

```bash
codex-sessions list
```

Example output:

```text
2026-02-22 13:48 - 2026-02-22 13:50 (UTC+00:00) - 019c8599-6845-7772-9c64-5f0ee47c73f1 - Add scope for type casting types
019c8599-6845-7772-9c64-5f0ee47c73f1 - Add scope for type casting types - NO ROLLOUT FILE
YYYY/MM/DD/rollout-....jsonl - NO ENTRY IN session_index.jsonl
```

For rollout files missing from `session_index.jsonl`, `list` infers a display
title from the first readable user/Codex message when possible, while still
marking the missing index entry.

Use a specific Codex home directory:

```bash
codex-sessions list --codex-home ~/.codex
```

Preview missing `session_index.jsonl` entries inferred from rollout files:

```bash
codex-sessions repair-index --dry-run
```

Apply those repairs:

```bash
codex-sessions repair-index
```

`repair-index --dry-run` does not modify Codex state. The real repair command
backs up `session_index.jsonl` under `backups/codex-sessions/`, appends missing
entries, and resets Codex state cache by moving root `state_*.sqlite*` files
into the same backup folder. If state cache reset fails, the index write is
rolled back; close all Codex sessions and retry.

Rename a session in `session_index.jsonl`:

```bash
codex-sessions rename 019dd5ce-19e1-78c3-9313-325228ddd983 "Better session title"
```

`rename` also updates the rollout `thread_name_updated` event when a rollout
file is available, backs up changed files under `backups/codex-sessions/`, and
resets Codex state cache. You can use an exact current title instead of an ID,
but if multiple sessions have that title the command will ask you to rerun with
one concrete ID.

Export one session as a transferable rollout JSONL file:

```bash
codex-sessions export 019ddf68-2bc0-75e2-aecb-22f49ca63c98 -o ./exports/
```

The exported filename is readable by default:

```text
2026-04-30--Fix-auto-parametrization-bug--019ddf68-2bc0-75e2-aecb-22f49ca63c98.jsonl
```

You can also use an exact current title or write to a specific file path:

```bash
codex-sessions export "Fix auto parametrization bug" -o ./session.jsonl
```

Export multiple sessions to a directory or zip archive:

```bash
codex-sessions export --all -o ./exports/
codex-sessions export --updated-after 2026-05-01 -o ./exports.zip
codex-sessions export --all --except 019ddf68-2bc0-75e2-aecb-22f49ca63c98 -o ./exports/
```

`export` writes a rollout copy without changing Codex state. If the current
`session_index.jsonl` title differs from the rollout title event, the exported
copy is updated so `import` can preserve that title on another machine. Existing
output files, colliding directory entries, and existing zip archives are refused
unless `--force` is passed.

Import a rollout JSONL file, a directory of rollout JSONL files, or an export
zip into Codex home:

```bash
codex-sessions import ./rollout-2026-04-30T18-20-39-019ddf68-2bc0-75e2-aecb-22f49ca63c98.jsonl
codex-sessions import ./exports/
codex-sessions import ./exports.zip
```

Preview the target path and index action without writing anything:

```bash
codex-sessions import --dry-run ./rollout.jsonl
```

Allow an existing local session to fast-forward when the imported rollout is
safely ahead:

```bash
codex-sessions import --merge ./exports.zip
```

`import` copies new rollouts into `sessions/YYYY/MM/DD/`, adds or updates the
matching `session_index.jsonl` entries when needed, updates rollout title
events to match the chosen titles, and resets Codex state cache once after
making backups under `backups/codex-sessions/`. Use `--name` to set the
imported title explicitly when importing one rollout file. Already-present
identical sessions are skipped. Duplicate session IDs inside one import input
are reported and refused as ambiguous. Existing sessions with different rollout
content are reported as conflicts and are not overwritten; other safe sessions
from the same bulk import are still imported. With `--merge`, imports also
fast-forward local rollouts when their comparable history is a prefix of the
incoming rollout. Equivalent histories and locally ahead histories are skipped;
diverged histories are reported and left untouched.

Search all Codex sessions:

```bash
codex-sessions find -i "dadata-sdk"
```

By default, `find` searches visible user and Codex messages only. Use
`--metadata` to also search compact session metadata such as cwd and repository
URL, `--tools` to also search concise tool call previews such as shell commands,
or `--all` to include both.

`grep` is an alias for `find`:

```bash
codex-sessions grep -i "dadata-sdk"
```

Use regex mode with `-r`, `--regex`, or the grep-style `-E` alias:

```bash
codex-sessions find -i -r "dadata-[a-z]+"
```

Adjust the maximum width of each matching line:

```bash
codex-sessions find --line-width 220 "dadata-sdk"
```

By default, `find` shows up to 5 matching lines per session. Use `-m` or
`--max-lines-per-session` to change the limit, or pass `0` to show all matching
lines.

Matches are highlighted with terminal colors by default when stdout is a
terminal, including Git Bash/MSYS terminals on Windows. Use `--color always` or
`--color never` to override auto-detection.

Search caches extracted searchable text under
`~/.codex/cache/codex-sessions/search-v3.json` and invalidates entries when the
source rollout file size or modification time changes. Use `--rebuild-cache` to
refresh cached entries, or `--no-cache` for a one-off uncached search.
`list` uses the same cache for rollout metadata and inferred titles.

## Codex Skill

This repo also includes a Codex skill that helps future Codex sessions inspect
previous conversations without loading large raw session files directly.

Install or update the skill from a local checkout:

```bash
mkdir -p ~/.codex/skills
cp -r skills/read-codex-session ~/.codex/skills/
```

In PowerShell:

```powershell
New-Item -ItemType Directory -Force $env:USERPROFILE\.codex\skills
Copy-Item -Recurse -Force .\skills\read-codex-session $env:USERPROFILE\.codex\skills\
```

After restarting Codex, ask for `$read-codex-session` or ask Codex to recover
context from an earlier conversation.

## Markdown Detail

`--md-include` controls broad optional sections:

```bash
# Visible user/Codex messages, reasoning, and progress messages.
codex-sessions --md-include dialogue input.jsonl -o output.md

# Default: dialogue plus concise tool call previews.
codex-sessions --md-include default input.jsonl -o output.md

# Add metadata tables such as turn_context and token_count.
codex-sessions --md-include metadata input.jsonl -o output.md

# Metadata plus raw unhandled records.
codex-sessions --md-include full input.jsonl -o output.md
```

`--md-tools` controls tool call/output detail:

```bash
# Tool names and call IDs only.
codex-sessions --md-tools names input.jsonl -o output.md

# Useful previews for known tool calls; unknown tools fall back to names.
codex-sessions --md-tools smart input.jsonl -o output.md

# Tool names plus truncated arguments and outputs.
codex-sessions --md-tools preview input.jsonl -o output.md

# Tune preview length.
codex-sessions --md-tools preview --md-tool-preview-chars 1200 input.jsonl -o output.md

# Hide tools entirely.
codex-sessions --md-tools none input.jsonl -o output.md
```

The default `--md-tools auto` follows `--md-include`: presets that include tools
render smart tool call previews, and presets without tools omit them. Explicit
`--md-tools` values override that behavior. Smart mode keeps tool outputs to
names and call IDs.

## Notes

- Encrypted reasoning payloads are redacted by default as `...` and rendered
  compactly in Markdown.
- Markdown metadata tables escape pipe characters and replace embedded newlines
  with `<br>`.
- The tool uses Rich for colored search output.

## License

MIT

## Development

Create a local virtual environment and install development tools:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run the test suite:

```bash
python -m unittest discover -s tests
```

Run the test suite with coverage:

```bash
python -m coverage run -m unittest discover -s tests
python -m coverage report -m
```

Run formatting, linting, and type checks:

```bash
python -m ruff format .
python -m ruff check .
python -m mypy
npx --yes pyright
```
