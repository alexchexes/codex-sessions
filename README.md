# Codex sessions

<!-- TOC -->

- [Codex sessions](#codex-sessions)
  - [Install](#install)
    - [Also install the skill](#also-install-the-skill)
  - [Update](#update)
    - [Also update the skill](#also-update-the-skill)
  - [Usage](#usage)
    - [Converter](#converter)
      - [Raw JSONL to YAML](#raw-jsonl-to-yaml)
      - [To Markdown](#to-markdown)
        - [Adjust Markdown details](#adjust-markdown-details)
        - [Timing annotations](#timing-annotations)
        - [Image handling](#image-handling)
    - [List sessions](#list-sessions)
      - [Repair Codex's index file](#repair-codexs-index-file)
    - [Rename sessions](#rename-sessions)
    - [Export sessions](#export-sessions)
    - [Import sessions](#import-sessions)
    - [Sync sessions](#sync-sessions)
    - [Search sessions](#search-sessions)
  - [Codex Skill](#codex-skill)
  - [Notes](#notes)
  - [License](#license)
  - [Development](#development)

<!-- /TOC -->

Inspect, search, repair, import/export, and convert Codex session files.

It can turn session rollout files from a Codex home directory such as `~/.codex/sessions/YYYY/MM/DD/rollout-<...>.jsonl` into readable YAML or dialogue-oriented Markdown:

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

### Also install the skill

```sh
codex-sessions install-skill
```

See [Codex skill](#codex-skill) for details.

## Update

Update an existing `pipx` install:

```bash
pipx upgrade codex-sessions
```

If `pipx` cannot reuse the original Git install spec, reinstall from GitHub:

```bash
pipx install --force git+https://github.com/alexchexes/codex-sessions.git
```

### Also update the skill

```sh
codex-sessions install-skill
```

## Usage

### Converter

The main executable command is `codex-sessions`.

The tool uses `CODEX_HOME` or the current user's `~/.codex` directory by default. To use a different Codex home, for example when testing against a copy instead of your real Codex state, pass it explicitly with `--codex-home ~/.codex`.

The converter target can be:

- rollout JSONL path, absolute or relative to Codex home dir:
  - `~/.codex/sessions/YYYY/MM/DD/rollout-<...>.jsonl`
  - `sessions/YYYY/MM/DD/rollout-<...>.jsonl`
- session ID: `019dd5ce-19e1-78c3-9313-325228ddd983`
- exact title: `"Fix schema mismatch for enums"`
- `latest` - the session whose rollout contains the most recent record timestamp. If rollout timestamps are unavailable, falls back to file modified time. The lookup reuses the session metadata cache, refreshing incomplete entries and entries whose rollout size or modified time changed.

#### Raw JSONL to YAML

If you need to inspect low-level session rollout records, convert the JSONL rollout to more readable YAML:

```bash
# default is conversion to YAML, no arguments except target are needed:
codex-sessions <ID-or-title-or-path>

# explicit output path:
codex-sessions <ID-or-title-or-path> -o rollout.yaml

# explicit YAML mode:
codex-sessions <ID-or-title-or-path> --yaml

# current directory as an output path:
codex-sessions <ID-or-title-or-path> -o ./

# specific Codex home directory:
codex-sessions --codex-home ~/.codex <ID-or-title-or-path>
```

When no output path is supplied, the tool writes under the Codex home directory, which defaults to `CODEX_HOME` or `~/.codex`. The converter normally writes to a path like `~/.codex/tmp/sessions/YYYY/MM/DD/rollout-<...>.yaml`.

Rollout files are useful for raw data inspection, but they are still noisy even as YAML. If you only need to inspect messages, tool calls, progress updates, etc, use the Markdown mode instead.

#### To Markdown

Use Markdown for a more concise and readable representation of the chat history.

```bash
# use default markdown settings and output path
codex-sessions <ID-or-title-or-path> --md

# if output name ends with `.md`, `--md` mode enabled automatically
codex-sessions <ID-or-title-or-path> -o path/for/output.md
```

##### Adjust Markdown details

`--md-include` controls broad optional sections:

```bash
# Visible user/Codex messages, reasoning, and progress messages.
codex-sessions <ID-or-title-or-path> --md --md-include dialogue

# Default: dialogue plus concise tool call previews.
codex-sessions <ID-or-title-or-path> --md --md-include default

# Add metadata tables such as turn_context and token_count.
codex-sessions <ID-or-title-or-path> --md --md-include metadata

# Metadata plus raw unhandled records.
codex-sessions <ID-or-title-or-path> --md --md-include full
```

`--md-tools` controls tool call/output detail:

```bash
# Tool names and call IDs only.
codex-sessions <ID-or-title-or-path> --md --md-tools names

# Useful previews for known tool calls; unknown tools fall back to names.
codex-sessions <ID-or-title-or-path> --md --md-tools smart

# Tool names plus truncated arguments and outputs.
codex-sessions <ID-or-title-or-path> --md --md-tools preview

# Tune preview length.
codex-sessions <ID-or-title-or-path> --md --md-tools preview --md-tool-preview-chars 1200

# Hide tools entirely.
codex-sessions <ID-or-title-or-path> --md --md-tools none
```

The default `--md-tools auto` follows `--md-include`: presets that include tools render smart tool call previews, and presets without tools omit them. Explicit `--md-tools` values override that behavior. Smart mode keeps tool outputs to names and call IDs.

##### Timing annotations

By default Markdown includes only low-noise timing markers: first/latest rollout record timestamps, long gaps between rendered events (4+ hrs), and tool durations that exceed the default threshold (30s+). You can add more details or adjust the defaults:

```bash
# Add timestamps to every rendered Markdown section heading:
codex-sessions <ID-or-title-or-path> --md --timestamps

# Tune idle-gap markers. Default: 4h.
codex-sessions <ID-or-title-or-path> --md --gap-threshold 2h

# Tune tool duration annotations. Default: 30s. Use 0 for all tool durations.
codex-sessions <ID-or-title-or-path> --md --tool-duration-threshold 0
```

##### Image handling

Images in the original rollout files are base64-encoded.

When converting to Markdown, base64 data is truncated so that large image payloads do not fill the `.md` file. The placeholder includes the original rollout path/line and a short base64 prefix.

To make images viewable in Markdown renderers, use extraction mode:

```bash
codex-sessions --md --md-images extract <ID-or-title-or-path>
```

Images are written to a sibling `<markdown-stem>_assets/` directory. Use `--md-images inline` only when you need to keep base64 image data inline in the Markdown.

### List sessions

List Codex sessions from `CODEX_HOME` or `~/.codex` and cross-check `session_index.jsonl` against actual session files:

```bash
codex-sessions list
```

Example output:

```text
2026-02-22 13:48 - 2026-02-22 13:50 (UTC+00:00) - 019c8599-6845-7772-9c64-5f0ee47c73f1 - Add scope for type casting types
019c8599-6845-7772-9c64-5f0ee47c73f1 - Add scope for type casting types - NO ROLLOUT FILE
YYYY/MM/DD/rollout-....jsonl - NO ENTRY IN session_index.jsonl
```

For rollout files missing from `session_index.jsonl`, `list` infers a display title from the first readable user/Codex message when possible, while still marking the missing index entry.

Use a specific Codex home directory:

```bash
codex-sessions list --codex-home ~/.codex
```

`list` and `find` flag rollouts whose stored session ID is missing or disagrees with the filename;
read-only inspection can continue, but state-changing commands may refuse them.

#### Repair Codex's index file

Preview missing `session_index.jsonl` entries inferred from rollout files:

```bash
codex-sessions repair-index --dry-run
```

Apply those repairs:

```bash
codex-sessions repair-index
```

`repair-index --dry-run` does not modify Codex state. The real repair command backs up `session_index.jsonl` under `backups/codex-sessions/`, appends missing entries, and resets Codex state cache by moving root `state_*.sqlite*` files into the same backup folder. Rollouts without a valid authoritative record-1 ID are warned about and skipped rather than indexed from a filename fallback. If state cache reset is blocked by a running Codex session, the repaired index stays written and the command prompts for a retry in an interactive terminal.

### Rename sessions

Rename a session in `session_index.jsonl`:

```bash
codex-sessions rename 019dd5ce-19e1-78c3-9313-325228ddd983 "Better session title"
```

`rename` also updates the rollout `thread_name_updated` event when a rollout file is available, backs up changed files under `backups/codex-sessions/`, and resets Codex state cache. It refuses to modify a rollout whose record-1 session metadata is invalid. You can use an exact current title instead of an ID, but if multiple sessions have that title the command will ask you to rerun with one concrete ID.

### Export sessions

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

`export` writes a rollout copy without changing Codex state. If the current `session_index.jsonl` title differs from the rollout title event, the exported copy is updated so `import` can preserve that title on another machine.

For backup safety, a damaged rollout with a usable trailing filename UUID is still exported, but its bytes are copied unchanged and a warning identifies the fallback. Import remains strict, so that backup must be repaired before it can be imported. A rollout with no usable metadata or filename ID is reported as a per-file failure. Bulk export continues with other files and returns status `1` when the resulting backup is incomplete; warnings for successfully copied degraded files do not change the success status. Existing output files, colliding directory entries, and existing zip archives are refused unless `--force` is passed.

### Import sessions

Import a rollout JSONL file, a directory of rollout JSONL files, or an export zip into Codex home:

```bash
codex-sessions import ./rollout-2026-04-30T18-20-39-019ddf68-2bc0-75e2-aecb-22f49ca63c98.jsonl
codex-sessions import ./exports/
codex-sessions import ./exports.zip
```

Preview the target path and index action without writing anything:

```bash
codex-sessions import --dry-run ./rollout.jsonl
```

Allow an existing local session to fast-forward when the imported rollout is safely ahead:

```bash
codex-sessions import --merge ./exports.zip
```

`import` requires a valid UUID in record-1 `session_meta.payload.id`; filename or later-metadata fallbacks are never used for state-changing imports. It copies new rollouts into `sessions/YYYY/MM/DD/`, adds or updates the matching `session_index.jsonl` entries when needed, updates rollout title events to match the chosen titles, and resets Codex state cache once after making backups under `backups/codex-sessions/`. Use `--name` to set the imported title explicitly when importing one rollout file. Already-present identical sessions are skipped. Duplicate session IDs inside one import input are reported and refused as ambiguous. Existing sessions with the same ID but different rollout content are reported as ID conflicts and are not overwritten; other safe sessions from the same bulk import are still imported. With `--merge`, imports also fast-forward local rollouts when their comparable history is a prefix of the incoming rollout. Equivalent histories and locally ahead histories are skipped; diverged histories are reported and left untouched. Add `--show-divergence` to include a compact preview of the first differing records for each diverged session.

### Sync sessions

Synchronize through a local folder:

```bash
codex-sessions sync ~/Dropbox/codex-sessions
codex-sessions sync --dry-run ~/Dropbox/codex-sessions
```

`sync` imports sessions found in the folder, exports local-only sessions back to that folder, and writes the same transfer manifest used by bulk export. It does not delete sessions from either side. Download/import identity is strict. Upload/export preserves damaged filename-ID rollouts unchanged with warnings, continues past no-ID failures, and returns status `1` if any local file could not be backed up. Same-ID sessions already present in the sync folder are compared by rollout history: identical/equivalent sessions are skipped, safely newer folder copies can fast-forward local state, local-ahead sessions stay local, and diverged conflicts are reported without overwriting either side. Degraded rollouts are never assigned a history relation: sync only recognizes byte-identical content already at the same output path, and it refuses to overwrite different valid or damaged content there.

Commands that change Codex sessions try to reset the state cache after writing their rollout or `session_index.jsonl` changes. If the root `state_*.sqlite*` files are locked, the successful session changes stay written. In an interactive terminal the command prompts after the lock failure so you can close Codex and retry. Use `--non-interactive` to avoid that prompt, or `--no-reset-state-cache` to skip the automatic attempt and control refresh from a script:

```bash
codex-sessions import --merge --no-reset-state-cache ./exports/
codex-sessions reset-state-cache
```

`reset-state-cache` backs up the live cache files before moving them out of Codex home and returns a nonzero status if the reset cannot run.

### Search sessions

Search all Codex sessions:

```bash
codex-sessions find -i "dadata-sdk"
```

By default, `find` searches visible user and Codex messages only. Use `--metadata` to also search compact session metadata such as cwd and repository URL, `--tools` to also search concise tool input/output previews such as shell commands and command output snippets, or `--all` to include both metadata and tools.

Limit search to one or more sessions with `--session`. Targets may be session IDs, exact titles, rollout paths, or `latest`:

```bash
codex-sessions find --session 019dd5ce-19e1-78c3-9313-325228ddd983 "needle"
codex-sessions find --session "Fix schema mismatch for enums" --tools "rg -n"
```

Use `--search-in` for precise target selection. Values are comma-separated and may include `visible`, `metadata`, `tool-inputs`, `tool-outputs`, `tools`, and `all`:

```bash
codex-sessions find --search-in tool-outputs "Traceback"
codex-sessions find --search-in tool-inputs,tool-outputs "mcp__ask_human.ask_human"
codex-sessions find --search-in metadata "copy-as-markdown"
```

`--search-in` cannot be combined with `--metadata`, `--tools`, or `--all`. Tool input/output search uses concise cached previews rather than full raw payloads, so cached search stays lightweight.

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

By default, `find` shows up to 5 matching lines per session. Use `-m` or `--max-lines-per-session` to change the limit, or pass `0` to show all matching lines.

The CLI uses terminal colors by default when stdout is a terminal, including Git Bash/MSYS terminals on Windows. `find` highlights matches; other commands use color for timestamps, section headings, secondary paths, and attention states. `find --color always` or `find --color never` overrides search highlighting auto-detection. Standard `NO_COLOR`, `CLICOLOR=0`, `FORCE_COLOR`, and `CLICOLOR_FORCE` environment flags apply to auto-detected CLI colors.

Search caches extracted searchable text and session metadata for speed. Use `--rebuild-cache` to refresh cached entries, or `--no-cache` for a one-off uncached search. `list` uses the same cache for rollout metadata and inferred titles.

## Codex Skill

This repo also includes a Codex skill that helps Codex inspect any existing conversations without reading large raw session rollouts directly.

Install or update the bundled Codex skill:

```bash
codex-sessions install-skill
```

This writes the `codex-sessions` skill to `~/.agents/skills`. Use `--skills-dir <path>` to choose a different user skill directory.

From a checkout, you can also run the command without installing the CLI:

```bash
PYTHONPATH=src python -m codex_sessions install-skill
```

For PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m codex_sessions install-skill
```

Codex usually detects skill changes automatically. If the skill does not appear, restart Codex. Then type `$` and select `$codex-sessions`, or ask Codex to do something with any of your Codex threads.

## Notes

- Encrypted reasoning payloads are redacted by default as `...` and rendered compactly in Markdown.
- Markdown metadata tables escape pipe characters and replace embedded newlines with `<br>`.
- The tool uses Rich for colored terminal output.

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

To make the normal `codex-sessions ...` command use source changes from this checkout immediately,
install the pipx app from the checkout in editable mode:

```bash
pipx uninstall codex-sessions
pipx install --editable .
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

When changing extracted session identity/metadata semantics or cache entry fields, bump both the
cache schema version and its versioned filename in `src/codex_sessions/sessions/cache.py` and
`src/codex_sessions/search/cache.py`. Add or update tests proving stale cache files are ignored
and rebuilt under the new identity rules.
