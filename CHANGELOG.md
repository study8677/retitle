# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [0.2.0] - 2026-05-29

### Added
- `retitle search <query>` — find sessions across Claude Code, Codex and Cursor
  at once, by title (fast) or with `--content` to grep message text, with
  highlighted matches and snippets.
- `retitle stats` — a one-glance overview: sessions per tool, untitled / stale
  counts, oldest active session, and how many retitle has renamed.
- `--json` output for `retitle list`, `retitle search` and `retitle stats`.
- `SECURITY.md` documenting the privacy/data-safety model and how to report issues.
- `ARCHITECTURE.md` explaining the layering and each tool's reverse-engineered storage.
- Ruff linting, enforced in CI.

## [0.1.0] - 2026-05-29

Initial release.

### Added
- Background renamer that retitles AI coding sessions once they go idle (default 5 minutes).
- Adapters for **Claude Code** (append-only `ai-title` lines), **Codex** (`state_*.sqlite`
  `threads.title`), and **Cursor** (`state.vscdb` composer headers + data).
- Naming backends: `heuristic` (default, offline, zero-dependency), `claude` / `codex` (CLI
  shell-out), and `anthropic` / `openai` (direct API).
- Idempotent engine: renames only when a session has new content since the last title, and
  respects titles edited by hand until the conversation moves on.
- CLI: `list`, `once`, `run`, `install`, `uninstall`, `status`, `config`.
- Background service install for macOS (launchd) and Linux (systemd).
- Zero runtime dependencies — pure standard library.
