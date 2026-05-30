import retitle.namers.cli_namer as cli_namer
from retitle.config import Config
from retitle.namers import get_namer


def _which(*available):
    avail = set(available)
    return lambda name: f"/usr/bin/{name}" if name in avail else None


def test_auto_prefers_claude(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("claude", "codex"))
    assert get_namer(Config(namer="auto")).name == "claude"


def test_auto_falls_back_to_codex(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("codex"))
    assert get_namer(Config(namer="auto")).name == "codex"


def test_auto_falls_back_to_heuristic_without_clis(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which())  # nothing installed
    assert get_namer(Config(namer="auto")).name == "heuristic"


def test_explicit_heuristic_ignores_clis(monkeypatch):
    monkeypatch.setattr(cli_namer.shutil, "which", _which("claude"))
    assert get_namer(Config(namer="heuristic")).name == "heuristic"


from retitle.models import Message  # noqa: E402

_MSGS = [Message("user", "add CSV export to the reports page and fix pagination")]


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_claude_uses_fast_model_and_clean_output(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        # claude -p prints just the answer text
        return _Proc(stdout="Add CSV export and fix pagination\n")

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("claude", {}).generate(_MSGS)
    assert title == "Add CSV export and fix pagination"
    assert seen["argv"][0] == "claude"
    assert "--model" in seen["argv"] and "haiku" in seen["argv"]


def test_claude_respects_model_override(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli_namer.subprocess, "run",
        lambda argv, **kw: seen.update(argv=argv) or _Proc(stdout="t\n"),
    )
    cli_namer.CliNamer("claude", {"model": "sonnet"}).generate(_MSGS)
    assert "sonnet" in seen["argv"] and "haiku" not in seen["argv"]


def test_codex_uses_output_last_message_and_default_model(monkeypatch):
    """codex must read its title from --output-last-message, not stdout, and
    default to the working fast Codex model."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        # codex streams a noisy transcript to stdout (must be ignored)…
        out_path = argv[argv.index("--output-last-message") + 1]
        # …and writes ONLY the final message to the file:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("CSV Export and Pagination Fix\n")
        return _Proc(stdout="[ts] codex\n[ts] tokens used: 2347\n")

    monkeypatch.setattr(cli_namer.subprocess, "run", fake_run)
    title = cli_namer.CliNamer("codex", {}).generate(_MSGS)
    assert title == "CSV Export and Pagination Fix"  # NOT "tokens used: 2347"
    assert "--output-last-message" in seen["argv"]
    assert "-m" in seen["argv"] and "gpt-5-codex" in seen["argv"]


def test_codex_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        cli_namer.subprocess, "run",
        lambda argv, **kw: _Proc(returncode=1, stderr="Unsupported model"),
    )
    assert cli_namer.CliNamer("codex", {"model": "bad"}).generate(_MSGS) is None
