from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

from .models import Decider, Driver, KrilinError, Snapshot, StaleSnapshot, Task, candidates, probability


@dataclass(frozen=True)
class Limits:
    max_steps: int = 20
    max_seconds: float = 60
    call_timeout: float = 8
    max_repeated_states: int = 3
    min_confidence: float = .8
    min_probability: float = .8

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 100 or not 0 < self.max_seconds <= 300:
            raise ValueError("Run limits must be 1–100 steps and up to 300 seconds")
        if not 0 < self.call_timeout <= 30 or self.max_repeated_states < 2:
            raise ValueError("Invalid timeout or repeated-state limit")
        probability(self.min_confidence)
        probability(self.min_probability)


@dataclass
class Result:
    run_id: str
    status: str
    reason: str
    steps: int
    elapsed_ms: int
    snapshot: dict[str, Any] | None
    history: list[dict[str, Any]]


class Runner:
    def __init__(self, driver: Driver, decider: Decider, limits: Limits = Limits(),
                 trace: Path | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.driver, self.decider, self.limits = driver, decider, limits
        self.trace, self.clock, self.sleep = trace, clock, sleep

    def run(self, task: Task) -> Result:
        start = self.clock()
        deadline = start + self.limits.max_seconds
        run_id = str(uuid4())
        history: list[dict[str, Any]] = []
        seen: Counter[str] = Counter()
        snapshot: Snapshot | None = None
        steps = 0

        def emit(event: dict[str, Any]) -> None:
            if self.trace:
                self.trace.parent.mkdir(parents=True, exist_ok=True)
                with self.trace.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"run_id": run_id, **event}, ensure_ascii=False) + "\n")

        def remaining() -> float:
            left = deadline - self.clock()
            if left <= 0:
                raise TimeoutError("Run deadline reached")
            return min(left, self.limits.call_timeout)

        def finish(status: str, reason: str) -> Result:
            result = Result(run_id, status, reason, steps, round((self.clock() - start) * 1000),
                            snapshot.state() if snapshot else None, history[-8:])
            emit({"event": "result", **asdict(result)})
            return result

        emit({"event": "start", "goal": task.goal, "assertions": [asdict(a) for a in task.assertions]})
        try:
            while True:
                observed_at = self.clock()
                snapshot = self.driver.observe(remaining())
                remaining()  # A late response must never authorize a further action.
                observe_ms = round((self.clock() - observed_at) * 1000)
                actions = candidates(snapshot, task)
                if all(a.satisfied(snapshot) for a in task.assertions):
                    return finish("succeeded", "All explicit UI assertions matched")
                if steps >= self.limits.max_steps:
                    return finish("escalated", "Step budget exhausted")
                scoped = replace(snapshot, elements=tuple(e for e in snapshot.elements if e.package in task.allowed_packages))
                if not scoped.elements:
                    # A known transition needs no model judgment. Keep observing within
                    # the same budgets until this app exposes a tree or the run expires.
                    steps += 1
                    self.sleep(min(.1, remaining()))
                    record = {"step": steps, "action": "wait_for_ui", "outcome": "waited",
                              "input": asdict(snapshot.input), "observe_ms": observe_ms}
                    history.append(record)
                    emit({"event": "step", **record})
                    continue
                fingerprint = scoped.fingerprint()
                seen[fingerprint] += 1
                if seen[fingerprint] >= self.limits.max_repeated_states:
                    return finish("escalated", "Repeated UI state or navigation cycle detected")
                state = {"goal": task.goal, "success_assertions": [asdict(a) for a in task.assertions],
                         "ui": scoped.state(), "recent_history": history[-8:]}
                decided_at = self.clock()
                decision = self.decider.decide(state, actions, remaining())
                remaining()
                decision_ms = round((self.clock() - decided_at) * 1000)
                for value in (decision.confidence, decision.selected_probability, decision.goal_probability):
                    probability(value)
                selected = next((a for a in actions if a.id == decision.action_id), None)
                if selected is None:
                    raise KrilinError("Decider returned an action outside the live candidate set")
                emit({"event": "decision", "decision": asdict(decision), "observe_ms": observe_ms, "decision_ms": decision_ms})
                if decision.confidence < self.limits.min_confidence or decision.selected_probability < self.limits.min_probability:
                    return finish("escalated", f"Decision below configured threshold: {selected.id}, confidence={decision.confidence:.3f}, probability={decision.selected_probability:.3f}")
                if selected.kind == "escalate":
                    return finish("escalated", "Decider requested help from the calling agent")
                steps += 1
                record = {"step": steps, "before": fingerprint, "action": selected.id,
                          "input": asdict(snapshot.input), "decision": asdict(decision),
                          "observe_ms": observe_ms, "decision_ms": decision_ms}
                acted_at = self.clock()
                if selected.kind == "wait":
                    self.sleep(min(.25, remaining()))
                    record["outcome"] = "waited"
                else:
                    try:
                        self.driver.execute(snapshot, selected, remaining())
                        record["outcome"] = "accepted"
                    except StaleSnapshot:
                        record["outcome"] = "stale_snapshot"
                        # A rejected token made no attempt on the UI. The step/deadline
                        # budgets still bound repeated races, without calling them a loop.
                        seen[fingerprint] -= 1
                record["action_ms"] = round((self.clock() - acted_at) * 1000)
                history.append(record)
                emit({"event": "step", **record})
                # No completion claim until another live observation verifies effects.
        except TimeoutError:
            return finish("escalated", "Run deadline reached")
        except KrilinError as exc:
            return finish("escalated", str(exc))
