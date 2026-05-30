"""Small, dependency-free helpers: paths, time, logging, text hygiene."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path

from .models import Message

APP = "retitle"


# --------------------------------------------------------------------------- #
# Paths (XDG with sensible fallbacks; works on macOS and Linux)
# --------------------------------------------------------------------------- #
def _base(env: str, default: str) -> Path:
    raw = os.environ.get(env)
    return Path(raw).expanduser() if raw else Path(default).expanduser()


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", "~/.config") / APP


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", "~/.local/state") / APP


def config_path() -> Path:
    return config_dir() / "config.toml"


def state_path() -> Path:
    return state_dir() / "state.json"


def log_path() -> Path:
    return state_dir() / "retitle.log"


def home() -> Path:
    return Path.home()


def now() -> float:
    return time.time()


def fmt_dur(seconds: float) -> str:
    """Human-friendly duration: '45s', '5m', '1.2h', '3.0d'."""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s / 3600:.1f}h".replace(".0h", "h")
    return f"{s / 86400:.1f}d".replace(".0d", "d")


# --------------------------------------------------------------------------- #
# Logging — quiet by default, structured enough to tail in a daemon log.
# --------------------------------------------------------------------------- #
_VERBOSE = False


def set_verbose(on: bool) -> None:
    global _VERBOSE
    _VERBOSE = on


def log(msg: str, *, level: str = "info") -> None:
    if level == "debug" and not _VERBOSE:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{level}] {msg}"
    stream = sys.stderr if level in ("warn", "error") else sys.stdout
    print(line, file=stream, flush=True)


# --------------------------------------------------------------------------- #
# Text hygiene — strip the noise that pollutes transcripts before we either
# hash them or hand them to a namer.
# --------------------------------------------------------------------------- #

# Tags injected by harnesses / wrappers that are not real user intent.
_TAG_BLOCK = re.compile(
    r"<(local-command-[^>]*|command-[^>]*|system-reminder|bash-[^>]*)>.*?</\1>",
    re.DOTALL,
)
_TAG_OPEN = re.compile(r"</?[a-z][a-z0-9-]*(?:\s[^>]*)?>", re.IGNORECASE)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_ABS_PATH = re.compile(r"(?:/[^\s/]+){2,}/?")
_URL = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")

# Acknowledgement-only messages carry no topic; ignore them when picking a title.
_TRIVIAL = {
    "ok", "okay", "k", "yes", "no", "y", "n", "yep", "yeah", "sure", "thanks",
    "thank you", "go", "go on", "continue", "next", "done", "good", "great",
    "nice", "cool", "stop", "wait", "a", "b", "c", "1", "2", "3",
    "好", "好的", "可以", "行", "继续", "嗯", "对", "是", "不", "没问题", "没事",
    "谢谢", "好吧", "ok的", "嗯嗯", "对的", "是的", "停", "等等", "下一步",
}


# System artifacts that masquerade as user/assistant turns: harness markers,
# tool notifications, command echoes, raw JSON/tool dumps. Never a real topic.
_NOISE_RE = re.compile(
    r"^\s*(?:"
    r"\[request interrupted"
    r"|\[no response"
    r"|<(?:subagent_notification|task-notification|system-reminder"
    r"|local-command-[a-z-]*|command-name|command-message|command-args"
    r"|command-stdout|bash-input|bash-stdout|bash-stderr|user_instructions"
    r"|environment_context|user-prompt-submit-hook)\b"
    r")",
    re.IGNORECASE,
)
_JSONISH = re.compile(r'^[\[{]\s*["{\[]')


def is_noise(text: str) -> bool:
    """True for harness/tool artifacts that are not genuine conversation."""
    if not text:
        return True
    t = text.lstrip()
    if not t:
        return True
    if _NOISE_RE.match(t):
        return True
    if _JSONISH.match(t):  # raw JSON / tool-result blob, not prose
        return True
    return False


def clean_text(text: str) -> str:
    """Reduce a raw message to readable prose suitable for titling/hashing."""
    if not text:
        return ""
    t = _TAG_BLOCK.sub(" ", text)
    t = _CODE_FENCE.sub(" ", t)
    t = _INLINE_CODE.sub(" ", t)
    t = _TAG_OPEN.sub(" ", t)
    t = _URL.sub(" ", t)
    t = _ABS_PATH.sub(" ", t)
    t = _WS.sub(" ", t)
    return t.strip()


def is_trivial(text: str) -> bool:
    """True for empty, noise, acknowledgement-only, or slash-command messages."""
    if is_noise(text):
        return True
    t = clean_text(text)
    if not t:
        return True
    low = t.lower().strip(" .!?。！？,，")
    if low in _TRIVIAL:
        return True
    if len(t) <= 1:
        return True
    if t.startswith("/"):  # a bare slash-command is trivial; one with args is not
        return len(t.split(None, 1)) == 1
    return False


def signature(messages: list[Message], *, tail: int = 24) -> str:
    """Stable hash of recent conversation content.

    Used to decide whether a session has gained *new* content since we last
    renamed it. Only the last ``tail`` messages are considered so that very
    long sessions still produce a cheap, change-sensitive fingerprint.
    """
    parts: list[str] = []
    for m in messages[-tail:]:
        parts.append(m.role)
        parts.append(clean_text(m.text)[:500])
    digest = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()
    return f"{len(messages)}:{digest[:16]}"


# --------------------------------------------------------------------------- #
# Title shaping — keep generated titles short, clean and display-friendly.
# --------------------------------------------------------------------------- #
_TITLE_MAX = 60


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def shape_title(raw: str, *, max_len: int = _TITLE_MAX) -> str:
    """Normalise any candidate title into a tidy one-liner."""
    if not raw:
        return ""
    t = raw.strip().strip("\"'`")
    # Collapse whitespace and drop surrounding quotes a model might add.
    t = _WS.sub(" ", t).strip()
    # Keep only the first line / sentence-ish chunk.
    t = t.split("\n")[0].strip()
    # Trim trailing punctuation that reads oddly in a title.
    t = t.rstrip(" .。!！?？,，:：;；")
    if len(t) > max_len:
        if _has_cjk(t):
            t = t[: max_len - 1].rstrip() + "…"
        else:
            cut = t[:max_len].rsplit(" ", 1)[0].rstrip()
            t = (cut or t[:max_len]).rstrip() + "…"
    # Capitalise leading latin letter; never force-case CJK.
    if t and not _has_cjk(t[:1]) and t[:1].isalpha():
        t = t[:1].upper() + t[1:]
    return t
