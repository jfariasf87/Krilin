from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Protocol


class KrilinError(Exception):
    """An actionable error that is safe to return to the calling agent."""


class StaleSnapshot(KrilinError):
    """The UI changed before execution; observe and decide again."""


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise KrilinError("Expected a numeric probability")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise KrilinError("Probability must be finite and between zero and one")
    return float(value)


@dataclass(frozen=True)
class Element:
    id: str
    package: str
    resource_id: str = ""
    text: str = ""
    description: str = ""
    role: str = ""
    enabled: bool = True
    checked: bool = False
    focused: bool = False
    password: bool = False
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class InputState:
    touch_exploration: bool = False
    accessibility_services: tuple[str, ...] = ()
    ime_visible: bool = False
    execution_mode: str = "semantic"


@dataclass(frozen=True)
class Snapshot:
    id: str
    elements: tuple[Element, ...]
    input: InputState
    active_package: str
    truncated: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        try:
            if data["protocol"] != 1 or not isinstance(data["snapshot_id"], str):
                raise ValueError("Unsupported bridge protocol")
            nodes = data["elements"]
            if not isinstance(nodes, list) or len(nodes) > 512:
                raise ValueError("Invalid element count")
            elements = []
            for node in nodes:
                for key in ("id", "package", "resource_id", "text", "description", "role"):
                    if not isinstance(node[key], str):
                        raise ValueError("Invalid element string")
                for key in ("enabled", "checked", "focused", "password"):
                    if type(node[key]) is not bool:
                        raise ValueError("Invalid element flag")
                if not isinstance(node["actions"], list) or any(
                    a not in {"click", "set_text", "scroll_forward", "scroll_backward"}
                    for a in node["actions"]
                ):
                    raise ValueError("Invalid element actions")
                elements.append(Element(**{**node, "actions": tuple(node["actions"])}))
            if len({e.id for e in elements}) != len(elements):
                raise ValueError("Duplicate element identifiers")
            inp = data["input"]
            if inp["execution_mode"] != "semantic":
                raise ValueError("Unsupported input mode")
            for key in ("touch_exploration", "ime_visible"):
                if type(inp[key]) is not bool:
                    raise ValueError("Invalid input flag")
            services = inp["accessibility_services"]
            if not isinstance(services, list) or not all(isinstance(s, str) for s in services):
                raise ValueError("Invalid accessibility services")
            if type(data["truncated"]) is not bool or not isinstance(data["active_package"], str):
                raise ValueError("Invalid snapshot metadata")
            return cls(data["snapshot_id"], tuple(elements),
                       InputState(**{**inp, "accessibility_services": tuple(services)}),
                       data["active_package"], data["truncated"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KrilinError(f"Invalid bridge snapshot: {exc}") from exc

    def state(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        # Snapshot tokens deliberately change every observation; progress must not.
        state = self.state()
        del state["id"]
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Assertion:
    package: str
    resource_id: str = ""
    text: str | None = None
    checked: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.package, str) or not self.package or not isinstance(self.resource_id, str):
            raise ValueError("Assertion package and resource_id must be strings")
        if self.text is not None and not isinstance(self.text, str):
            raise ValueError("Assertion text must be a string")
        if not (self.resource_id or self.text is not None):
            raise ValueError("Assertions need a package and a resource_id or exact text")
        if self.checked is not None and type(self.checked) is not bool:
            raise ValueError("checked must be a boolean")

    def satisfied(self, snapshot: Snapshot) -> bool:
        matches = [e for e in snapshot.elements if e.package == self.package
                   and (not self.resource_id or e.resource_id == self.resource_id)
                   and (self.text is None or e.text == self.text)
                   and (self.checked is None or e.checked == self.checked)]
        return len(matches) == 1


@dataclass(frozen=True)
class Task:
    goal: str
    allowed_packages: tuple[str, ...]
    assertions: tuple[Assertion, ...]
    text_values: dict[str, str] = field(default_factory=dict)
    allow_back: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip() or len(self.goal) > 4000:
            raise ValueError("Goal must contain 1–4000 characters")
        if not self.allowed_packages or not all(isinstance(p, str) and p for p in self.allowed_packages):
            raise ValueError("At least one allowed package is required")
        if not self.assertions or any(a.package not in self.allowed_packages for a in self.assertions):
            raise ValueError("Provide success assertions within the allowed packages")
        if type(self.allow_back) is not bool:
            raise ValueError("allow_back must be a boolean")
        if any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 2000
               for k, v in self.text_values.items()):
            raise ValueError("text_values must map resource IDs to strings of at most 2000 characters")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        if not isinstance(data, dict) or not isinstance(data.get("allowed_packages"), list) or not isinstance(data.get("assertions"), list):
            raise ValueError("Task requires allowed_packages and assertions arrays")
        if not isinstance(data.get("text_values", {}), dict):
            raise ValueError("text_values must be an object")
        return cls(goal=data["goal"], allowed_packages=tuple(data["allowed_packages"]),
                   assertions=tuple(Assertion(**a) for a in data["assertions"]),
                   text_values=data.get("text_values", {}), allow_back=data.get("allow_back", False))


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    target: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class Decision:
    action_id: str
    confidence: float
    selected_probability: float
    goal_probability: float
    model: str
    usage: dict[str, int | float] = field(default_factory=dict)


class Driver(Protocol):
    def observe(self, timeout: float) -> Snapshot: ...
    def execute(self, snapshot: Snapshot, action: Action, timeout: float) -> None: ...


class Decider(Protocol):
    def decide(self, state: dict[str, Any], actions: tuple[Action, ...], timeout: float) -> Decision: ...


def candidates(snapshot: Snapshot, task: Task) -> tuple[Action, ...]:
    if snapshot.truncated:
        raise KrilinError("UI tree exceeds the observation limit; narrow the task or driver scope")
    if snapshot.active_package not in task.allowed_packages:
        raise KrilinError(f"Foreground package is outside task scope: {snapshot.active_package}")
    actions = [Action("wait", "wait"), Action("escalate", "escalate")]
    if task.allow_back:
        actions.append(Action("back", "back"))
    for e in snapshot.elements:
        if not e.enabled or e.password or e.package not in task.allowed_packages:
            continue
        for kind in e.actions:
            text = None
            if kind == "set_text":
                text = task.text_values.get(e.resource_id)
                if text is None or text == e.text:
                    continue
            actions.append(Action(f"{kind}:{e.id}", kind, e.id, text))
    if len(actions) > 255:
        raise KrilinError("More than 255 action candidates; narrow the task or driver scope")
    return tuple(actions)
