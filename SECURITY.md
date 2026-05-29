# Security Policy

`retitle` reads and writes your local AI coding session stores, so its safety and
privacy are a first-class concern.

## What retitle does with your data

- **Runs entirely on your machine.** With the default `heuristic` namer, nothing
  ever leaves your computer. There is no telemetry, ever.
- **Only changes titles.** It appends or updates a single title field per session
  and never edits, deletes, or reorders your conversations.
- **Network only on opt-in.** The `anthropic` / `openai` namers send a short
  transcript excerpt to that API *only if you set an API key*; the `claude` /
  `codex` namers go through CLIs you have already authorized.
- **Conservative writes.** Reads use read-only SQLite connections; writes use a
  busy timeout and a single atomic transaction; it only ever touches *idle*
  sessions.

## Reporting a vulnerability

Please report security issues **privately** via GitHub Security Advisories
(the "Report a vulnerability" button under the repository's **Security** tab),
not a public issue.

Include what you found, the affected version (`retitle --version`), and steps to
reproduce. ⚠️ Please **redact any private session content** from your report.

We aim to acknowledge reports within a few days and to fix verified issues
promptly.

## Supported versions

retitle is pre-1.0; security fixes land on the latest `main` and the newest
release.
