"""The rename engine: decide which idle sessions need a fresh title, and apply.

Per-session decision (see `_assess`, which never calls the namer):

  1. Idle gate — skip if used within `idle_seconds` (still in use).
  2. No-activity short-circuit — skip if `last_active` is unchanged since we last
     evaluated it (cheap: no transcript read needed).
  3. Substance — skip if too few real user messages.
  4. Unchanged — skip if the content hash matches the title we last wrote.
  5. Otherwise it's a *candidate*: ask the namer for a title and write it back
     if it differs.

Assessment (fast, local) is deliberately separated from naming (slow — it shells
out to `claude`/`codex` or an API). A pass sorts candidates most-recent-first and
renames at most `limit` of them, so the background daemon stays responsive even
with thousands of old sessions, and `retitle once --limit N` renames just a batch.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from . import util
from .adapters.base import Adapter
from .config import Config
from .models import RenamePlan, Session
from .namers.base import Namer
from .state import StateStore


class Engine:
    def __init__(
        self,
        cfg: Config,
        adapters: list[Adapter],
        namer: Namer,
        state: StateStore,
    ):
        self.cfg = cfg
        self.adapters = adapters
        self.namer = namer
        self.state = state

    # -- assessment (never calls the namer) -------------------------------- #
    def _assess(self, adapter: Adapter, s: Session, now_ts: float):
        """Return (status, sig, msgs) without naming. status is one of
        active | no-activity | thin | unchanged | candidate."""
        if s.idle_seconds(now_ts) < self.cfg.idle_seconds:
            return ("active", None, None)
        prev = self.state.get(adapter.name, s.id)
        if prev and prev.get("seen_active") == s.last_active:
            return ("no-activity", None, None)
        msgs = adapter.read_transcript(s)
        substantive = [
            m for m in msgs if m.role == "user" and not util.is_trivial(m.text)
        ]
        if len(substantive) < self.cfg.min_user_messages:
            return ("thin", None, msgs)
        sig = util.signature(msgs)
        if prev and prev.get("content_sig") == sig:
            return ("unchanged", sig, msgs)
        return ("candidate", sig, msgs)

    def _name(self, adapter: Adapter, s: Session, msgs: list) -> str:
        raw = self.namer.generate(
            substantive_only(msgs), old_title=s.title, cwd=s.cwd, tool=adapter.name
        )
        return util.shape_title(raw or "")

    def _record_skip(self, adapter, s, status, sig, now_ts) -> None:
        fields: dict = {"last_seen": now_ts}
        if status in ("thin", "unchanged"):
            fields["seen_active"] = s.last_active
        if status == "unchanged" and sig:
            fields["content_sig"] = sig
        self.state.update(adapter.name, s.id, **fields)

    # -- planning (used by `retitle list` for preview) --------------------- #
    def _plan_one(self, adapter: Adapter, s: Session, now_ts: float) -> RenamePlan:
        status, sig, msgs = self._assess(adapter, s, now_ts)
        if status == "active":
            return RenamePlan(
                s, "skip", reason=f"active ({util.fmt_dur(s.idle_seconds(now_ts))} idle)"
            )
        if status == "no-activity":
            return RenamePlan(s, "skip", reason="no activity since last check")
        if status == "thin":
            return RenamePlan(
                s, "skip", mark_seen=True, reason="no substantive user messages"
            )
        if status == "unchanged":
            return RenamePlan(
                s, "skip", content_sig=sig, mark_seen=True, reason="unchanged since last rename"
            )
        title = self._name(adapter, s, msgs)
        if not title:
            return RenamePlan(s, "skip", reason="namer returned nothing")
        if s.title and title.casefold() == s.title.casefold():
            return RenamePlan(
                s,
                "skip",
                new_title=title,
                content_sig=sig,
                mark_seen=True,
                reason="already current",
            )
        return RenamePlan(
            s,
            "rename",
            new_title=title,
            content_sig=sig,
            mark_seen=True,
            reason=f"idle {util.fmt_dur(s.idle_seconds(now_ts))}",
        )

    def plan(self, now_ts: float | None = None):
        now_ts = util.now() if now_ts is None else now_ts
        since = now_ts - self.cfg.max_age_days * 86400
        plans: list[tuple[Adapter, RenamePlan]] = []
        alive: set[tuple[str, str]] = set()
        healthy: set[str] = set()
        for adapter in self.adapters:
            try:
                sessions = adapter.discover(since)
            except Exception as exc:
                util.log(f"{adapter.name}: discover failed: {exc}", level="warn")
                continue
            healthy.add(adapter.name)
            for s in sessions:
                alive.add((adapter.name, s.id))
                try:
                    plans.append((adapter, self._plan_one(adapter, s, now_ts)))
                except Exception as exc:
                    util.log(
                        f"{adapter.name}: planning {s.short_id} failed: {exc}", level="warn"
                    )
        return plans, alive, healthy

    # -- the rename pass --------------------------------------------------- #
    def tick(self, limit: int | None = None, progress: bool = False) -> tuple[int, int]:
        """Run one pass. Renames at most `limit` candidates, most-recent first.
        limit=None uses cfg.batch_size; limit=0 (or batch_size 0) means no cap.
        Returns (renamed, total_candidates)."""
        now_ts = util.now()
        since = now_ts - self.cfg.max_age_days * 86400
        if limit is None:
            limit = self.cfg.batch_size or 0

        candidates: list[tuple[Adapter, Session, str, list]] = []
        alive: set[tuple[str, str]] = set()
        healthy: set[str] = set()
        for adapter in self.adapters:
            try:
                sessions = adapter.discover(since)
            except Exception as exc:
                util.log(f"{adapter.name}: discover failed: {exc}", level="warn")
                continue
            healthy.add(adapter.name)
            for s in sessions:
                alive.add((adapter.name, s.id))
                try:
                    status, sig, msgs = self._assess(adapter, s, now_ts)
                except Exception as exc:
                    util.log(
                        f"{adapter.name}: assessing {s.short_id} failed: {exc}", level="warn"
                    )
                    continue
                if status == "candidate":
                    candidates.append((adapter, s, sig, msgs))
                elif not self.cfg.dry_run:
                    self._record_skip(adapter, s, status, sig, now_ts)

        candidates.sort(key=lambda c: c[1].last_active, reverse=True)
        total = len(candidates)
        if limit:
            candidates = candidates[:limit]
        if progress and total:
            extra = (
                f" (of {total}; use --limit N or --all for more)"
                if len(candidates) < total
                else ""
            )
            util.log(f"naming {len(candidates)} session(s){extra} via '{self.namer.name}'…")

        renamed = 0
        for i, (adapter, s, sig, msgs) in enumerate(candidates, 1):
            try:
                title = self._name(adapter, s, msgs)
            except Exception as exc:
                util.log(f"{adapter.name}: naming {s.short_id} failed: {exc}", level="warn")
                continue
            if not title:
                continue  # transient namer miss — retry next pass
            if s.title and title.casefold() == s.title.casefold():
                if not self.cfg.dry_run:
                    self.state.update(
                        adapter.name, s.id,
                        content_sig=sig, seen_active=s.last_active,
                        title=title, last_seen=now_ts,
                    )
                continue
            if self.cfg.dry_run:
                util.log(f"[dry-run] {adapter.name} {s.short_id}: {s.title!r} → {title!r}")
                continue
            try:
                adapter.set_title(s, title)
            except Exception as exc:
                util.log(f"{adapter.name}: rename {s.short_id} failed: {exc}", level="warn")
                continue
            self.state.update(
                adapter.name, s.id,
                content_sig=sig, seen_active=s.last_active,
                title=title, renamed_at=now_ts, last_seen=now_ts,
            )
            renamed += 1
            tag = f"[{i}/{len(candidates)}] " if progress else ""
            util.log(f"{tag}{adapter.name} {s.short_id}: {s.title!r} → {title!r}")

        if not self.cfg.dry_run:
            self.state.prune(alive, healthy)
            self.state.save()
        return renamed, total

    def run_forever(self, stop: Callable[[], bool] | None = None) -> None:
        util.log(
            f"retitle started — idle={util.fmt_dur(self.cfg.idle_seconds)}, "
            f"poll={util.fmt_dur(self.cfg.poll_seconds)}, namer={self.namer.name}, "
            f"batch={self.cfg.batch_size or '∞'}, tools={[a.name for a in self.adapters]}"
            + (" [DRY-RUN]" if self.cfg.dry_run else "")
        )
        while True:
            try:
                renamed, total = self.tick()
                if renamed:
                    more = f" ({total - renamed} more queued)" if total > renamed else ""
                    util.log(f"renamed {renamed} session(s){more}")
            except Exception as exc:
                util.log(f"pass failed: {exc}", level="error")
            if stop and stop():
                break
            time.sleep(self.cfg.poll_seconds)


def substantive_only(msgs: list) -> list:
    """Drop trivial acknowledgement turns so the namer sees real intent."""
    return [m for m in msgs if not (m.role == "user" and util.is_trivial(m.text))]
