"""Command-line interface for retitle."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import config as config_mod
from . import service, util
from .adapters import all_adapters, get_adapters
from .engine import Engine
from .namers import NAMER_NAMES, get_namer
from .state import StateStore


# --------------------------------------------------------------------------- #
# tiny tty helpers (no dependencies)
# --------------------------------------------------------------------------- #
def _tty() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty() else text


def bold(s: str) -> str:
    return _c(s, "1")


def dim(s: str) -> str:
    return _c(s, "2")


def green(s: str) -> str:
    return _c(s, "32")


def trunc(text: str, width: int) -> str:
    text = text or "—"
    text = text.replace("\n", " ")
    if len(text) > width:
        return text[: width - 1] + "…"
    return text


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
def _apply_overrides(cfg: config_mod.Config, args) -> config_mod.Config:
    if getattr(args, "idle", None) is not None:
        cfg.idle_seconds = args.idle
    if getattr(args, "interval", None) is not None:
        cfg.poll_seconds = args.interval
    if getattr(args, "namer", None):
        cfg.namer = args.namer
    if getattr(args, "tool", None):
        cfg.tools = tuple(args.tool)
    if getattr(args, "max_age_days", None) is not None:
        cfg.max_age_days = args.max_age_days
    if getattr(args, "dry_run", False):
        cfg.dry_run = True
    return cfg


def _build(cfg: config_mod.Config):
    adapters = get_adapters(cfg)
    namer = get_namer(cfg)
    state = StateStore()
    return adapters, namer, state, Engine(cfg, adapters, namer, state)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    config_mod.ensure_default()
    cfg = _apply_overrides(config_mod.load(), args)
    util.set_verbose(args.verbose)
    adapters, namer, state, engine = _build(cfg)
    if not adapters:
        util.log("no supported tools found on this machine.", level="warn")
        return 1
    if args.once:
        renamed, total = engine.tick()
        util.log(f"done — {renamed} renamed of {total} considered")
        return 0
    try:
        engine.run_forever()
    except KeyboardInterrupt:
        util.log("stopped")
    return 0


def cmd_list(args) -> int:
    cfg = _apply_overrides(config_mod.load(), args)
    util.set_verbose(args.verbose)
    adapters, namer, state, engine = _build(cfg)
    if not adapters:
        print("No supported tools found (Claude Code, Codex, Cursor).")
        return 1

    plans, _, _ = engine.plan()
    by_tool: dict[str, list] = {}
    for adapter, plan in plans:
        by_tool.setdefault(adapter.label, []).append(plan)

    now = util.now()
    would_rename = 0
    print()
    for label, group in by_tool.items():
        group.sort(key=lambda p: p.session.last_active, reverse=True)
        print(bold(label))
        for plan in group[: args.limit]:
            s = plan.session
            idle = util.fmt_dur(s.idle_seconds(now))
            cur = trunc(s.title or "—", 36)
            if plan.action == "rename":
                would_rename += 1
                right = green("→ " + (plan.new_title or ""))
            elif plan.reason.startswith("active"):
                right = dim("· active")
            else:
                right = dim("· " + plan.reason)
            print(f"  {idle:>6}  {cur:<36}  {right}")
        if len(group) > args.limit:
            print(dim(f"  … and {len(group) - args.limit} more"))
        print()

    print(
        dim(
            f"{would_rename} session(s) would be renamed next pass "
            f"(idle ≥ {util.fmt_dur(cfg.idle_seconds)}, namer={namer.name}). "
            "Run `retitle once` to apply, or `retitle install` to do it continuously."
        )
    )
    return 0


def cmd_status(args) -> int:
    cfg = config_mod.load()
    print(bold(f"retitle {__version__}"))

    cp = util.config_path()
    cp_note = "" if cp.exists() else dim("  (using defaults; `retitle config` to create)")
    print(f"  config : {cp}{cp_note}")

    sp = util.state_path()
    tracked = 0
    if sp.exists():
        try:
            data = json.loads(sp.read_text("utf-8"))
            tracked = sum(len(v) for v in data.values())
        except (json.JSONDecodeError, OSError):
            pass
    print(f"  state  : {sp}  ({tracked} tracked)")
    print(f"  log    : {util.log_path()}")
    print(
        f"  config : idle={util.fmt_dur(cfg.idle_seconds)}  "
        f"poll={util.fmt_dur(cfg.poll_seconds)}  namer={cfg.namer}"
    )
    print(f"  {service.status_line()}")

    print(bold("  tools:"))
    enabled = set(cfg.tools)
    for adapter in all_adapters():
        avail = green("found") if adapter.available() else dim("not found")
        suffix = "" if adapter.name in enabled else dim("  [disabled in config]")
        print(f"    {adapter.label:<13} {avail}{suffix}")
    return 0


def cmd_config(args) -> int:
    path = config_mod.ensure_default()
    if args.path:
        print(path)
        return 0
    print(f"# {path}\n")
    print(path.read_text("utf-8"))
    return 0


def cmd_install(args) -> int:
    config_mod.ensure_default()
    return service.install()


def cmd_uninstall(args) -> int:
    return service.uninstall()


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #
def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--tool",
        action="append",
        choices=config_mod.ALL_TOOLS,
        help="limit to specific tool(s); repeatable",
    )
    sp.add_argument("--namer", choices=NAMER_NAMES, help="override the namer")
    sp.add_argument("--idle", type=int, metavar="SEC", help="idle threshold (seconds)")
    sp.add_argument(
        "--max-age-days",
        dest="max_age_days",
        type=int,
        help="only consider sessions active within N days",
    )
    sp.add_argument("-v", "--verbose", action="store_true", help="verbose logging")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="retitle",
        description="Auto-rename idle Claude Code, Codex & Cursor sessions "
        "to match what they're actually about.",
    )
    p.add_argument("-V", "--version", action="version", version=f"retitle {__version__}")
    sub = p.add_subparsers(dest="cmd")

    pr = sub.add_parser("run", help="run the renamer (daemon by default)")
    _add_common(pr)
    pr.add_argument("--once", action="store_true", help="single pass, then exit")
    pr.add_argument("--interval", type=int, metavar="SEC", help="seconds between passes")
    pr.add_argument("--dry-run", action="store_true", help="log changes without writing")
    pr.set_defaults(func=cmd_run)

    po = sub.add_parser("once", help="single pass, then exit (alias for `run --once`)")
    _add_common(po)
    po.add_argument("--dry-run", action="store_true", help="log changes without writing")
    po.set_defaults(func=cmd_run, once=True)

    pl = sub.add_parser("list", help="preview sessions and the titles retitle would set")
    _add_common(pl)
    pl.add_argument("--limit", type=int, default=40, help="max rows per tool")
    pl.set_defaults(func=cmd_list, dry_run=True)

    ps = sub.add_parser("status", help="show config, detected tools and daemon status")
    ps.set_defaults(func=cmd_status)

    pc = sub.add_parser("config", help="create/show the config file")
    pc.add_argument("--path", action="store_true", help="print the config path only")
    pc.set_defaults(func=cmd_config)

    pi = sub.add_parser("install", help="install the background service")
    pi.set_defaults(func=cmd_install)

    pu = sub.add_parser("uninstall", help="remove the background service")
    pu.set_defaults(func=cmd_uninstall)

    return p


_DEFAULTS = {
    "once": False,
    "interval": None,
    "dry_run": False,
    "idle": None,
    "namer": None,
    "tool": None,
    "max_age_days": None,
    "verbose": False,
    "limit": 40,
    "path": False,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    for attr, default in _DEFAULTS.items():
        if not hasattr(args, attr):
            setattr(args, attr, default)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
