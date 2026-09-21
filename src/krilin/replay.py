"""Replay a recorded run offline: the same snapshots and Jev answers, through the real loop.

Record with `krilin run --record --trace file.jsonl` (or scripts/evaluate.py --record). A recording is
evidence of one run; replaying it exercises the runner's policy against real trees without a device or key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import Action, Decision, KrilinError, Snapshot, Task


class ReplayDriver:
    def __init__(self, snapshots: Iterable[Snapshot]) -> None:
        self._snapshots = list(snapshots)
        self.executed: list[Action] = []

    def observe(self, timeout: float) -> Snapshot:
        if not self._snapshots:
            raise KrilinError("Recording exhausted: no further observation")
        return self._snapshots.pop(0)

    def execute(self, snapshot: Snapshot, action: Action, timeout: float) -> None:
        self.executed.append(action)


class ReplayDecider:
    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._decisions = list(decisions)
        self.states: list[dict[str, Any]] = []

    def decide(self, state: dict[str, Any], actions: tuple[Action, ...], timeout: float) -> Decision:
        self.states.append(state)
        if not self._decisions:
            raise KrilinError("Recording exhausted: no further decision")
        return self._decisions.pop(0)


def run_ids(path: Path) -> list[str]:
    """Runs in a trace, in order; a scenario trace holds one run per step."""
    seen: dict[str, None] = {}
    for event in _events(path):
        if event.get("event") == "start":
            seen.setdefault(event["run_id"], None)
    return list(seen)


def load_recording(path: Path, run_id: str | None = None) -> tuple[Task, ReplayDriver, ReplayDecider]:
    events = _events(path)
    if run_id is None:
        ids = run_ids(path)
        if not ids:
            raise KrilinError("Trace contains no run")
        run_id = ids[0]
    mine = [e for e in events if e.get("run_id") == run_id]
    start = next((e for e in mine if e.get("event") == "start" and "task" in e), None)
    if start is None:
        raise KrilinError("Recording has no task; it predates --record support")
    snapshots = [Snapshot.from_dict({**e["snapshot"], "protocol": 1, "snapshot_id": e["snapshot"]["id"]})
                 for e in mine if e.get("event") == "observation"]
    if not snapshots:
        raise KrilinError("Recording has no observations; run with --record")
    decisions = [Decision(**e["decision"]) for e in mine if e.get("event") == "decision"]
    return Task.from_dict(start["task"]), ReplayDriver(snapshots), ReplayDecider(decisions)


def _events(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise KrilinError(f"Unreadable trace: {exc}") from exc
