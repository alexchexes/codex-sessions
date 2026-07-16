---
name: codex-sessions
description: Search, inspect, summarize, or recover context from previous Codex conversations or from the current conversation before context compaction.
---

# Codex Sessions

Use this skill to inspect Codex session history without loading raw rollout JSONL into context.

## Workflow

1. Resolve the target session:
   - If the user gives a rollout path, session ID, exact thread title, or `latest`, pass it directly to `codex-sessions`.
   - Session IDs and exact titles can resolve archived sessions. `latest` intentionally considers active sessions only.
   - If the user gives a topic, partial ID, or partial title, use `codex-sessions find` first, then rerun conversion with a concrete session ID. Do not guess between multiple fuzzy matches.

2. Prepare compact Markdown with the installed CLI. When the user only asks to read a conversation, default to dialogue-only:

```bash
codex-sessions <target> --md --md-include dialogue
```

If the request also clearly requires full input/output for one known tool, add only that tool with a quoted glob:

```bash
codex-sessions <target> --md --md-include dialogue --md-tools full --md-tool-include '*<tool-name-glob>*'
```

The command writes Markdown under `$CODEX_HOME/tmp/sessions/...` by default and prints the path. If that location is not writable, rerun with `-o <output.md>` under a writable task or workspace directory.

3. Read the generated Markdown, not the raw JSONL. Prefer targeted reads with `rg`, `Select-String`, `Get-Content -TotalCount`, or equivalent before loading a large file.

4. Summarize only the session facts needed for the user request. Mention when tool outputs were omitted or previewed.

## Detail Levels

Default Markdown output uses `--md-tools auto`: visible dialogue plus `smart` tool previews (`smart` keeps tool rendering compact, shows useful previews for known tool inputs, and falls back to names for unknown tools). It also includes first/latest rollout record timestamps, long gap markers (4+ hrs), and long tool durations (30s+).

Use a higher-detail pass only when exact arguments, exact output, metadata, or record ordering matters:

```bash
codex-sessions <target> --md --timestamps
codex-sessions <target> --md --tool-duration-threshold 0
codex-sessions <target> --md --md-tools names
codex-sessions <target> --md --md-tools smart
codex-sessions <target> --md --md-tools preview --md-tool-preview-chars 1200
codex-sessions <target> --md --md-tools full
codex-sessions <target> --md --md-tools full --md-tool-include '*ask_human*'
codex-sessions <target> --md --md-include dialogue,+reasoning
codex-sessions <target> --md --md-include metadata
```

Use `--timestamps` for timeline recovery. Use `--tool-duration-threshold 0` only when every tool duration matters. Both `dialogue` and `default` omit reasoning records; add `+reasoning` explicitly when they matter, while `full` includes them automatically. `--md-tool-include` accepts case-sensitive shell-style glob patterns (quoted to prevent shell expansion); values without glob characters are exact matches. It filters enabled tools without enabling them itself.

Use `--md-include metadata` only when turn context, token counts, cwd, model, or rate-limit information matters. Metadata-inclusive renders can be very noisy in long sessions because repeated turn contexts and token-count records may dwarf the dialogue.

Base64 data images are truncated by default. Use `--md-images extract` when image content matters and the Markdown should link to real image files. Use `--md-images inline` only when the renderer must receive self-contained Markdown; inline image notes point back to `--md-images truncate` and `--md-images extract` for cleanup.

Use `--format yaml` only for targeted structured inspection, such as fields not indexed by search or event ordering that Markdown does not expose clearly.

## Search

When the user asks to find a previous conversation by topic or phrase, prefer `codex-sessions find` before raw `rg` over JSONL:

```bash
codex-sessions find -i "search phrase"
codex-sessions find -i -r "regex|pattern"
codex-sessions find --tools "shell command"
codex-sessions find --metadata "repository-or-cwd"
codex-sessions find --session <target> --search-in tool-inputs,tool-outputs "tool-or-needle"
codex-sessions find --search-in visible,tool-inputs,tool-outputs --tool-include '*ask_human*' "needle"
codex-sessions find --archives exclude "active-only needle"
codex-sessions find --archives only "archived-only needle"
```

`find` searches deserialized visible messages by default, highlights matches, groups results by session, and caches extracted text under `$CODEX_HOME/cache/codex-sessions/`. It includes archived sessions by default; archived results are labeled `ARCHIVED`. Use `--search-in` for precise targets: `visible`, `metadata`, `tool-inputs`, `tool-outputs`, `tools`, or `all`. Use `--tool-include` with quoted, case-sensitive shell-style globs to restrict enabled tool targets without filtering visible messages; values without glob characters remain exact matches, and ask-human input previews include its question and context. For broad research, start with `find --all`, `--session`, `--line-width`, and `-m 0`, then render only the sessions that look relevant.

Use raw `rg` only for narrow file-format checks, missing record types, exact raw event fields/order not exposed clearly by Markdown, or fields not covered by `find`.

If `list` or `find` shows `NO ENTRY IN session_index.jsonl`, inspect proposed index repairs with:

```bash
codex-sessions repair-index --dry-run
```

Treat `FILENAME ID MISMATCH`, `INVALID RECORD-1...` as rollout-integrity warnings. Record-1 `session_meta.payload.id` is authoritative; `repair-index` skips rollouts without a canonical record-1 ID; also it skips archived sessions.

Run `codex-sessions repair-index` only when the user wants to modify Codex state. It backs up `session_index.jsonl` and appends the missing entries without rebuilding Codex's state database automatically.

`repair-index`, `rename`, `import`, and `sync` offer an optional state database rebuild after a successful local mutation only in an interactive terminal. The prompt confirms that all Codex writers are closed and defaults to no; `--non-interactive` and `--no-reset-state-cache` skip the offer.

Treat `codex-sessions reset-state-cache` as explicit, lossy recovery. It resolves the live database directory from `--sqlite-home`, Codex `config.toml`, `CODEX_SQLITE_HOME`, or Codex home; backs up and moves aside its `state_*.sqlite*` family; and can lose DB-only state such as agent jobs, closed subagent status, and exact archive times. Run it only with all Codex writers closed. It asks for confirmation interactively and requires `--yes` when non-interactive. A compatible-prefix cross-device import or sync fast-forward does not require a rebuild for conversation context, although SQLite list metadata can remain stale until the next real turn. Normal filesystem-backed Codex thread listing can discover newly copied rollouts without a rebuild; a state-only client view may need a filesystem-backed listing or restart.

## Direct Paths

When the target is already a rollout path and the user requests a custom output location, pass the path and `-o` directly:

```bash
codex-sessions <rollout.jsonl> --md --md-tools smart -o <output.md>
codex-sessions <rollout.jsonl> --md --md-tools preview --md-tool-preview-chars 1200 -o <output.md>
codex-sessions <rollout.jsonl> --md --md-images extract -o <output.md>
```

Avoid opening raw JSONL except for narrow targeted searches such as finding a missing record type.
