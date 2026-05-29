"""The rename engine: decide which idle sessions need a fresh title, and apply.

Decision rule for one session:

  1. Skip if it has been idle for less than ``idle_seconds`` (still in use).
  2. Skip if nothing has changed since we last looked at it (cheap mtime check).
  3. Skip if it has too few substantive user messages to be worth naming.
  4. Skip if its content hash matches the title we last wrote (nothing new).
  5. Otherwise generate a title and, if it differs, write it back.

This makes the whole thing idempotent and quietly respectful of manual edits:
a hand-typed title survives until the conversation actually moves on.
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

    # -- planning ----------------------------------------------------------- #
    def _plan_one(self, adapter: Adapter, s: Session, now_ts: float) -> RenamePlan:
        idle = s.idle_seconds(now_ts)
        if idle < self.cfg.idle_seconds:
            return RenamePlan(s, "skip", reason=f"active ({util.fmt_dur(idle)} idle)")

        prev = self.state.get(adapter.name, s.id)
        # If nothing has changed since we last fully evaluated this session,
        # skip without re-reading the transcript. seen_active is only stored
        # after a real evaluation (never while a session was merely active), so
        # a match here genuinely means "no new activity".
        if prev and prev.get("seen_active") == s.last_active:
            return RenamePlan(s, "skip", reason="no activity since last check")

        msgs = adapter.read_transcript(s)
        substantive = [
            m for m in msgs if m.role == "user" and not util.is_trivial(m.text)
        ]
        if len(substantive) < self.cfg.min_user_messages:
            return RenamePlan(s, "skip", mark_seen=True, reason="no substantive user messages")

        sig = util.signature(msgs)
        if prev and prev.get("content_sig") == sig:
            return RenamePlan(
                s, "skip", content_sig=sig, mark_seen=True, reason="unchanged since last rename"
            )

        raw = self.namer.generate(
            substantive_only(msgs), old_title=s.title, cwd=s.cwd, tool=adapter.name
        )
        title = util.shape_title(raw or "")
        if not title:
            # Could be a transient namer failure — don't cache, retry next pass.
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
            reason=f"idle {util.fmt_dur(idle)}",
        )

    def plan(
        self, now_ts: float | None = None
    ) -> tuple[list[tuple[Adapter, RenamePlan]], set, set]:
        now_ts = util.now() if now_ts is None else now_ts
        since = now_ts - self.cfg.max_age_days * 86400
        plans: list[tuple[Adapter, RenamePlan]] = []
        alive: set[tuple[str, str]] = set()
        healthy: set[str] = set()  # adapters that discovered without error
        for adapter in self.adapters:
            try:
                sessions = adapter.discover(since)
            except Exception as exc:  # one bad tool shouldn't sink the pass
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

    # -- applying ----------------------------------------------------------- #
    def _record(self, adapter: Adapter, plan: RenamePlan, now_ts: float) -> None:
        s = plan.session
        fields: dict = {"last_seen": now_ts}
        if plan.mark_seen:
            fields["seen_active"] = s.last_active
        if plan.content_sig:
            fields["content_sig"] = plan.content_sig
        if plan.new_title:
            fields["title"] = plan.new_title
        if plan.action == "rename":
            fields["renamed_at"] = now_ts
        self.state.update(adapter.name, s.id, **fields)

    def tick(self) -> tuple[int, int]:
        """Run a single pass. Returns (renamed_count, considered_count)."""
        now_ts = util.now()
        plans, alive, healthy = self.plan(now_ts)
        renamed = 0
        for adapter, plan in plans:
            if plan.action == "rename":
                if self.cfg.dry_run:
                    util.log(
                        f"[dry-run] {adapter.name} {plan.session.short_id}: "
                        f"{plan.session.title!r} -> {plan.new_title!r}"
                    )
                    continue
                try:
                    adapter.set_title(plan.session, plan.new_title)
                    self._record(adapter, plan, now_ts)
                    renamed += 1
                    util.log(
                        f"{adapter.name} {plan.session.short_id}: "
                        f"{plan.session.title!r} -> {plan.new_title!r}"
                    )
                except Exception as exc:
                    util.log(
                        f"{adapter.name}: rename {plan.session.short_id} failed: {exc}",
                        level="warn",
                    )
            else:
                if not self.cfg.dry_run:
                    self._record(adapter, plan, now_ts)
                util.log(
                    f"skip {adapter.name} {plan.session.short_id}: {plan.reason}",
                    level="debug",
                )
        if not self.cfg.dry_run:
            self.state.prune(alive, healthy)
            self.state.save()
        return renamed, len(plans)

    def run_forever(self, stop: Callable[[], bool] | None = None) -> None:
        util.log(
            f"retitle started — idle={util.fmt_dur(self.cfg.idle_seconds)}, "
            f"poll={util.fmt_dur(self.cfg.poll_seconds)}, namer={self.namer.name}, "
            f"tools={[a.name for a in self.adapters]}"
            + (" [DRY-RUN]" if self.cfg.dry_run else "")
        )
        while True:
            try:
                renamed, total = self.tick()
                if renamed:
                    util.log(f"renamed {renamed} of {total} session(s)")
            except Exception as exc:
                util.log(f"pass failed: {exc}", level="error")
            if stop and stop():
                break
            time.sleep(self.cfg.poll_seconds)


def substantive_only(msgs: list) -> list:
    """Drop trivial acknowledgement turns so the namer sees real intent."""
    return [m for m in msgs if not (m.role == "user" and util.is_trivial(m.text))]
