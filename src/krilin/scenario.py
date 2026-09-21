"""Ordered subgoals as data. Code launches the app and sequences steps; Jev still decides every UI action."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import time
from typing import Any, Callable

from .models import KrilinError, Task
from .runner import Limits, Result, Runner

MAX_STEPS = 50
MAX_SECONDS = 900


@dataclass(frozen=True)
class Launch:
    """A stated precondition executed by code, never a choice the model makes."""

    package: str
    activity: str | None = None
    clear_task: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.package, str) or not self.package:
            raise ValueError("launch.package must be a non-empty string")
        if self.activity is not None and (not isinstance(self.activity, str) or not self.activity):
            raise ValueError("launch.activity must be a non-empty string")
        if type(self.clear_task) is not bool:
            raise ValueError("launch.clear_task must be a boolean")

    @classmethod
    def from_dict(cls, data: Any) -> Launch:
        if not isinstance(data, dict) or not set(data) <= {"package", "activity", "clear_task"}:
            raise ValueError("launch fields are package, activity and clear_task")
        return cls(**data)


@dataclass(frozen=True)
class Step:
    task: Task
    max_steps: int = 20
    max_seconds: float = 60

    def __post_init__(self) -> None:
        Limits(max_steps=self.max_steps, max_seconds=self.max_seconds)  # Same ranges as a single run.


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: tuple[Step, ...]
    launch: Launch | None = None
    max_seconds: float = 300

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("Scenario name must contain 1–200 characters")
        if not 1 <= len(self.steps) <= MAX_STEPS or any(not isinstance(s, Step) for s in self.steps):
            raise ValueError(f"A scenario has 1–{MAX_STEPS} steps")
        if isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float)) \
                or not 0 < self.max_seconds <= MAX_SECONDS:
            raise ValueError(f"Scenario max_seconds must be within 0–{MAX_SECONDS}")
        allowed = {p for step in self.steps for p in step.task.allowed_packages}
        if self.launch and self.launch.package not in allowed:
            raise ValueError("launch.package must be one of the steps' allowed packages")

    @classmethod
    def from_dict(cls, data: Any) -> Scenario:
        known = {"name", "steps", "launch", "max_seconds", "allowed_packages", "allow_back"}
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            raise ValueError("Scenario requires a steps array")
        if not set(data) <= known:
            raise ValueError(f"Scenario fields are {', '.join(sorted(known))}")
        steps = []
        for index, step in enumerate(data["steps"], 1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be an object")
            step = dict(step)
            for key in ("allowed_packages", "allow_back"):
                if key not in step and key in data:
                    step[key] = data[key]  # Scenario-level defaults for every step.
            budget = {key: step.pop(key) for key in ("max_steps", "max_seconds") if key in step}
            steps.append(Step(Task.from_dict(step), **budget))
        launch = Launch.from_dict(data["launch"]) if data.get("launch") is not None else None
        return cls(data.get("name", "scenario"), tuple(steps), launch, data.get("max_seconds", 300))


@dataclass
class ScenarioResult:
    name: str
    status: str
    reason: str
    failed_step: int | None  # 0 means the launch precondition failed.
    steps: list[Result]
    elapsed_ms: int
    usage: dict[str, int | float]


def run_scenario(scenario: Scenario, driver: Any, decider: Any, launcher: Callable[[Launch], None] | None = None,
                 limits: Limits = Limits(), trace: Path | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, record: bool = False) -> ScenarioResult:
    start = clock()
    results: list[Result] = []
    usage: dict[str, int | float] = {"calls": 0, "cost": 0, "input_tokens": 0, "output_tokens": 0}

    def emit(event: dict[str, Any]) -> None:
        if trace:
            trace.parent.mkdir(parents=True, exist_ok=True)
            with trace.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"scenario": scenario.name, **event}, ensure_ascii=False) + "\n")

    def finish(status: str, reason: str, failed_step: int | None) -> ScenarioResult:
        elapsed = round((clock() - start) * 1000)
        emit({"event": "scenario_result", "status": status, "reason": reason, "failed_step": failed_step,
              "elapsed_ms": elapsed, "usage": usage})
        return ScenarioResult(scenario.name, status, reason, failed_step, results, elapsed, usage)

    if scenario.launch:
        if launcher is None:
            raise KrilinError("The scenario declares a launch precondition, but no device launcher is configured")
        emit({"event": "launch", **asdict(scenario.launch)})
        try:
            launcher(scenario.launch)
        except KrilinError as exc:
            return finish("escalated", f"Launch failed: {exc}", 0)
    for index, step in enumerate(scenario.steps, 1):
        remaining = scenario.max_seconds - (clock() - start)
        if remaining <= 0:
            return finish("escalated", "Scenario deadline reached", index)
        emit({"event": "scenario_step", "step": index, "goal": step.task.goal})
        step_limits = replace(limits, max_steps=step.max_steps, max_seconds=min(step.max_seconds, remaining))
        result = Runner(driver, decider, step_limits, trace, clock, sleep, record).run(step.task)
        results.append(result)
        for key in usage:
            usage[key] += result.usage.get(key, 0)
        if result.status != "succeeded":
            return finish("escalated", f"Step {index} {result.status}: {result.reason}", index)
    return finish("succeeded", f"All {len(scenario.steps)} steps verified", None)
