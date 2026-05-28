---
name: codex-sessions
description: Search, inspect, summarize, or recover context from previous Codex conversations or from the current conversation before context compaction.
---

# Codex Sessions

Use this skill to inspect Codex session history without loading raw rollout JSONL into context.

## Workflow

1. Resolve the target session:
   - If the user gives a rollout path, session ID, exact thread title, or
     `latest`, pass it directly to `codex-sessions`.
   - If the user gives a topic, partial ID, or partial title, use
     `codex-sessions find` first, then rerun conversion with a concrete session
     ID. Do not guess between multiple fuzzy matches.

2. Prepare compact Markdown with the installed CLI:

```bash
codex-sessions <target> --md
```

The command writes Markdown under `$CODEX_HOME/tmp/sessions/...` by default and
prints the path.

3. Read the generated Markdown, not the raw JSONL. Prefer targeted reads with `rg`, `Select-String`, `Get-Content -TotalCount`, or equivalent before loading a large file.

4. Summarize only the session facts needed for the user request. Mention when tool outputs were omitted or previewed.

## Detail Levels

Default Markdown output uses `--md-tools auto`: visible dialogue plus smart tool
previews.

Use a higher-detail pass only when needed:

```bash
codex-sessions <target> --md --md-tools names
codex-sessions <target> --md --md-tools smart
codex-sessions <target> --md --md-tools preview --md-tool-preview-chars 1200
codex-sessions <target> --md --md-tools full
codex-sessions <target> --md --md-include metadata
```

Use `--md-include metadata` only when turn context, token counts, cwd, model, or
rate-limit information matters. Metadata-inclusive renders can be very noisy in
long sessions because repeated turn contexts and token-count records may dwarf
the dialogue.

Base64 data images are truncated by default. Use `--md-images extract` when
image content matters and the Markdown should link to real image files. Use
`--md-images inline` only when the renderer must receive self-contained
Markdown; inline image notes point back to `--md-images truncate` and
`--md-images extract` for cleanup.

Use `--format yaml` only when the user asks for raw structured inspection.

## Search

When the user asks to find a previous conversation by topic or phrase, prefer
`codex-sessions find` before raw `rg` over JSONL:

```bash
codex-sessions find -i "search phrase"
codex-sessions find -i -r "regex|pattern"
codex-sessions find --tools "shell command"
codex-sessions find --metadata "repository-or-cwd"
```

`find` searches deserialized visible messages by default, highlights matches,
groups results by session, and caches extracted text under
`$CODEX_HOME/cache/codex-sessions/`. Use raw `rg` only for narrow file-format
checks or when searching fields not covered by `find`.

If `list` or `find` shows `NO ENTRY IN session_index.jsonl`, inspect proposed
index repairs with:

```bash
codex-sessions repair-index --dry-run
```

Run `codex-sessions repair-index` only when the user wants to modify Codex
state. It backs up `session_index.jsonl` and renames root `state_*.sqlite*`
files so Codex rebuilds its state cache.

## Direct Paths

When the target is already a rollout path and the user requests a custom output
location, pass the path and `-o` directly:

```bash
codex-sessions <rollout.jsonl> --md --md-tools smart -o <output.md>
codex-sessions <rollout.jsonl> --md --md-tools preview --md-tool-preview-chars 1200 -o <output.md>
codex-sessions <rollout.jsonl> --md --md-images extract -o <output.md>
```

Avoid opening raw JSONL except for narrow targeted searches such as finding a missing record type.
