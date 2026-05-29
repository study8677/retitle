"""Install retitle as a background service (launchd on macOS, systemd on Linux)."""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from . import util

LABEL = "com.github.retitle"
_PASS_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _program_args() -> list[str]:
    # `-m retitle` is robust regardless of where the console script landed.
    return [sys.executable, "-m", "retitle", "run"]


def _launch_agent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "retitle.service"


def _passthrough_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    for key in _PASS_ENV:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def install() -> int:
    if sys.platform == "darwin":
        return _install_launchd()
    if sys.platform.startswith("linux"):
        return _install_systemd()
    util.log(
        f"auto-install unsupported on {sys.platform}; "
        "run `retitle run` under your own process manager.",
        level="warn",
    )
    return 1


def uninstall() -> int:
    if sys.platform == "darwin":
        return _uninstall_launchd()
    if sys.platform.startswith("linux"):
        return _uninstall_systemd()
    util.log(f"nothing to uninstall on {sys.platform}")
    return 0


# -- macOS / launchd -------------------------------------------------------- #
def _install_launchd() -> int:
    plist = _launch_agent_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    log = util.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "Label": LABEL,
        "ProgramArguments": _program_args(),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": _passthrough_env(),
    }
    with open(plist, "wb") as fh:
        plistlib.dump(spec, fh)
    plist.chmod(0o600)  # may embed API keys — keep it owner-only
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    res = subprocess.run(
        ["launchctl", "load", "-w", str(plist)], capture_output=True, text=True
    )
    if res.returncode != 0:
        util.log(f"launchctl load failed: {res.stderr.strip()}", level="warn")
        return 1
    util.log(f"installed launchd agent → {plist}")
    util.log(f"logs → {log}")
    return 0


def _uninstall_launchd() -> int:
    plist = _launch_agent_plist()
    if not plist.exists():
        util.log("no launchd agent installed")
        return 0
    subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True)
    plist.unlink()
    util.log(f"removed {plist}")
    return 0


# -- Linux / systemd -------------------------------------------------------- #
def _install_systemd() -> int:
    unit = _systemd_unit()
    unit.parent.mkdir(parents=True, exist_ok=True)
    exec_start = " ".join(shlex.quote(a) for a in _program_args())
    env_lines = "\n".join(
        f'Environment="{k}={v}"' for k, v in _passthrough_env().items()
    )
    unit.write_text(
        f"""[Unit]
Description=retitle — auto-rename idle AI coding sessions
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
{env_lines}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
""",
        "utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    res = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "retitle.service"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        util.log(f"systemctl enable failed: {res.stderr.strip()}", level="warn")
        return 1
    util.log(f"installed systemd user service → {unit}")
    return 0


def _uninstall_systemd() -> int:
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "retitle.service"],
        capture_output=True,
    )
    unit = _systemd_unit()
    if unit.exists():
        unit.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    util.log("removed systemd user service")
    return 0


def status_line() -> str:
    if sys.platform == "darwin":
        if not _launch_agent_plist().exists():
            return "daemon: not installed (launchd)"
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        running = LABEL in (res.stdout or "")
        return f"daemon: {'running' if running else 'installed (not running)'} (launchd)"
    if sys.platform.startswith("linux"):
        if not _systemd_unit().exists():
            return "daemon: not installed (systemd)"
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "retitle.service"],
            capture_output=True,
            text=True,
        )
        return f"daemon: {res.stdout.strip() or 'unknown'} (systemd)"
    return "daemon: manual (auto-install unsupported on this platform)"
