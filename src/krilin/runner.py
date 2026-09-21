from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

from .models import (Action, Decider, Driver, KrilinError, Snapshot, StaleSnapshot, Task, brief, candidates,
                     input_targets, nearest, probability)

STALE_RETRIES = 3


@dataclass(frozen=True)
class Limits:
    max_steps: int = 20
    max_seconds: float = 60
    call_timeout: float = 8
    max_repeated_states: int = 3
    max_waits: int = 8
    max_effect_waits: int = 3
    min_confidence: float = .8
    min_probability: float = .8

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 100 or not 0 < self.max_seconds <= 300:
            raise ValueError("Run limits must be 1–100 steps and up to 300 seconds")
        if not 0 < self.call_timeout <= 30 or self.max_repeated_states < 2 or self.max_waits < 1 or self.max_effect_waits < 0:
            raise ValueError("Invalid timeout, repeated-state or wait limit")
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
    usage: dict[str, int | float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def diagnose(task: Task, snapshot: Snapshot, candidate_count: int, screens: int) -> dict[str, Any]:
    """What a caller needs to fix an escalated task without reading the source."""
    unmet = []
    for assertion in task.assertions:
        if assertion.satisfied(snapshot):
            continue
        matches = len(assertion.matches(snapshot))
        if assertion.absent:
            hint = f"{matches} element(s) still match; wait for them to disappear or narrow the selector"
        elif matches > 1:
            hint = f"{matches} elements match; add role, resource_id or more text so exactly one does"
        else:
            hint = "no element matches; compare with the nearest elements or check the goal reached this screen"
        unmet.append({"assertion": assertion.to_dict(), "matches": matches, "hint": hint,
                      "nearest": [brief(e) for e in nearest(assertion, snapshot)]})
    return {"candidates": candidate_count, "screens": screens, "ambiguous_inputs": input_targets(snapshot, task)[1],
            "unmet_assertions": unmet}


def model_history(history: list[dict[str, Any]], current_input: dict[str, Any]) -> list[dict[str, Any]]:
    """What Jev is told about earlier steps: actions and their UI-level outcomes.

    Stale rejections are runner-internal (nothing reached the UI), and timings, fingerprints and
    earlier probability tables carry no decision value. Input state is repeated only when it differed.
    """
    visible = []
    for record in history:
        if record.get("outcome") == "stale_snapshot":
            continue
        item = {"step": record["step"], "action": record["action"], "outcome": record["outcome"]}
        if record.get("input") != current_input:
            item["input"] = record["input"]
        visible.append(item)
    return visible[-8:]


class Runner:
    def __init__(self, driver: Driver, decider: Decider, limits: Limits = Limits(),
                 trace: Path | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, record: bool = False) -> None:
        self.driver, self.decider, self.limits = driver, decider, limits
        self.trace, self.clock, self.sleep, self.record = trace, clock, sleep, record

    def run(self, task: Task) -> Result:
        start = self.clock()
        deadline = start + self.limits.max_seconds
        run_id = str(uuid4())
        history: list[dict[str, Any]] = []
        seen: Counter[str] = Counter()
        snapshot: Snapshot | None = None
        steps = 0
        candidate_count = 0
        usage: dict[str, int | float] = {"calls": 0, "cost": 0, "input_tokens": 0, "output_tokens": 0}
        # A decision rejected as stale, kept for re-dispatch while the scoped UI stays identical.
        retry: tuple[Action, str, dict[str, Any], int] | None = None
        # The state Jev chose to wait on: revisiting it is a pending transition, not a navigation cycle.
        waiting_on: str | None = None
        waits = 0
        # The state before the last accepted action: seeing it again right after is usually latency.
        acted_on: str | None = None
        effect_waits = 0
        idle_polls = 0  # Consecutive observations with nothing to act on.

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
            diagnostics = diagnose(task, snapshot, candidate_count, len(seen)) if status != "succeeded" and snapshot else {}
            result = Result(run_id, status, reason, steps, round((self.clock() - start) * 1000),
                            snapshot.state() if snapshot else None, history[-8:], usage, diagnostics)
            emit({"event": "result", **asdict(result)})
            return result

        def execute(selected: Action, record: dict[str, Any]) -> bool:
            acted_at = self.clock()
            try:
                self.driver.execute(snapshot, selected, remaining())
                record["outcome"] = "accepted"
                return True
            except StaleSnapshot:
                # A rejected token made no attempt on the UI. The step/deadline budgets
                # still bound repeated races, without calling them a loop.
                record["outcome"] = "stale_snapshot"
                return False
            finally:
                record["action_ms"] = round((self.clock() - acted_at) * 1000)
                history.append(record)
                emit({"event": "step", **record})

        emit({"event": "start", "goal": task.goal, "assertions": [a.to_dict() for a in task.assertions],
              "task": task.to_dict()})
        try:
            while True:
                observed_at = self.clock()
                snapshot = self.driver.observe(remaining())
                remaining()  # A late response must never authorize a further action.
                observe_ms = round((self.clock() - observed_at) * 1000)
                if self.record:
                    emit({"event": "observation", "snapshot": snapshot.state(), "observe_ms": observe_ms})
                actions = candidates(snapshot, task)
                candidate_count = len(actions)
                if all(a.satisfied(snapshot) for a in task.assertions):
                    return finish("succeeded", "All explicit UI assertions matched")
                if steps >= self.limits.max_steps:
                    return finish("escalated", "Step budget exhausted")
                scoped = replace(snapshot, elements=tuple(e for e in snapshot.elements if e.package in task.allowed_packages))
                if not scoped.elements:
                    # A known transition needs no model judgment and spends no step budget. Keep
                    # observing until this app exposes a tree or the run deadline expires.
                    self.sleep(min(.1, remaining()))
                    record = {"step": steps, "action": "wait_for_ui", "outcome": "waited",
                              "input": asdict(snapshot.input), "observe_ms": observe_ms}
                    history.append(record)
                    emit({"event": "step", **record})
                    continue
                fingerprint = scoped.fingerprint()
                if all(a.kind in ("wait", "escalate", "back") for a in actions) and idle_polls < self.limits.max_effect_waits:
                    # Nothing to act on while the assertions are unmet: usually a window still being
                    # populated (a dialog, a transition). Look again before asking Jev to judge it.
                    idle_polls += 1
                    self.sleep(min(.25, remaining()))
                    record = {"step": steps, "action": "wait_for_actions", "outcome": "waited",
                              "input": asdict(snapshot.input), "observe_ms": observe_ms}
                    history.append(record)
                    emit({"event": "step", **record})
                    continue
                idle_polls = 0
                if acted_on == fingerprint and effect_waits < self.limits.max_effect_waits:
                    # An accepted action whose effect is not visible yet: give the app a moment
                    # before asking Jev to judge an unchanged screen.
                    effect_waits += 1
                    self.sleep(min(.25, remaining()))
                    record = {"step": steps, "action": "wait_for_effect", "outcome": "waited",
                              "input": asdict(snapshot.input), "observe_ms": observe_ms}
                    history.append(record)
                    emit({"event": "step", **record})
                    continue
                acted_on, effect_waits = None, 0
                if retry and retry[1] == fingerprint and retry[3] < STALE_RETRIES and any(a.id == retry[0].id for a in actions):
                    # Only geometry moved since Jev chose this action (the device compares both);
                    # the same semantic UI needs no second opinion, and the device still revalidates.
                    selected, decision_state, attempts = retry[0], retry[2], retry[3] + 1
                    steps += 1
                    record = {"step": steps, "before": fingerprint, "action": selected.id, "input": asdict(snapshot.input),
                              "decision": decision_state, "retry_after_stale": attempts, "observe_ms": observe_ms}
                    if execute(selected, record):
                        retry, acted_on = None, fingerprint
                    else:
                        retry = (selected, fingerprint, decision_state, attempts)
                    continue
                retry = None
                if waiting_on == fingerprint:
                    waits += 1
                    if waits >= self.limits.max_waits:
                        return finish("escalated", f"Waited {waits} times without a UI change")
                else:
                    waiting_on, waits = None, 0
                    seen[fingerprint] += 1
                    if seen[fingerprint] >= self.limits.max_repeated_states:
                        return finish("escalated", "Repeated UI state or navigation cycle detected")
                state = {"goal": task.goal, "success_assertions": [a.to_dict() for a in task.assertions],
                         "ui": scoped.compact(), "recent_history": model_history(history, asdict(snapshot.input))}
                decided_at = self.clock()
                decision = self.decider.decide(state, actions, remaining())
                remaining()
                decision_ms = round((self.clock() - decided_at) * 1000)
                usage["calls"] += 1
                for key in ("cost", "input_tokens", "output_tokens"):
                    usage[key] += decision.usage.get(key, 0)
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
                waiting_on = fingerprint if selected.kind == "wait" else None
                if selected.kind == "wait":
                    acted_at = self.clock()
                    self.sleep(min(.25, remaining()))
                    record.update(outcome="waited", action_ms=round((self.clock() - acted_at) * 1000))
                    history.append(record)
                    emit({"event": "step", **record})
                elif execute(selected, record):
                    acted_on = fingerprint
                else:
                    seen[fingerprint] -= 1
                    retry = (selected, fingerprint, asdict(decision), 0)
                # No completion claim until another live observation verifies effects.
        except TimeoutError:
            return finish("escalated", "Run deadline reached")
        except KrilinError as exc:
            return finish("escalated", str(exc))
