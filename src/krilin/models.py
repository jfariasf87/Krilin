from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Iterable, Protocol


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


def normalize(value: str) -> str:
    """Case-folded text with whitespace runs collapsed, for substring selectors."""
    return " ".join(value.split()).casefold()


def role_name(role: str) -> str:
    """`android.widget.Button` -> `Button`; Flutter and Compose report the same class names."""
    return role.rsplit(".", 1)[-1]


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


def brief(element: Element) -> str:
    """One line a caller can turn into a selector."""
    parts = [element.id, role_name(element.role) or "View"]
    if element.resource_id:
        parts.append(f"id={element.resource_id}")
    if element.text:
        parts.append(f"text={element.text[:60]!r}")
    if element.description:
        parts.append(f"desc={element.description[:60]!r}")
    if element.checked:
        parts.append("checked")
    if not element.enabled:
        parts.append("disabled")
    if element.actions:
        parts.append("[" + ",".join(element.actions) + "]")
    return " ".join(parts)


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

    def compact(self) -> dict[str, Any]:
        """What Jev reads: the same facts as state(), without token, defaults or empty fields."""
        elements = []
        for e in self.elements:
            item: dict[str, Any] = {"id": e.id}
            if e.package != self.active_package:
                item["package"] = e.package
            for key in ("resource_id", "text", "description"):
                if getattr(e, key):
                    item[key] = getattr(e, key)
            if e.role:
                item["role"] = role_name(e.role)
            if not e.enabled:
                item["enabled"] = False
            for flag in ("checked", "focused", "password"):
                if getattr(e, flag):
                    item[flag] = True
            if e.actions:
                item["actions"] = list(e.actions)
            elements.append(item)
        return {"active_package": self.active_package, "input": asdict(self.input), "elements": elements}

    def fingerprint(self) -> str:
        # Snapshot tokens deliberately change every observation; progress must not.
        state = self.state()
        del state["id"]
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:20]


SELECTOR_FIELDS = ("resource_id", "text", "text_contains", "description", "description_contains", "role", "checked")


@dataclass(frozen=True)
class Selector:
    """Identifies UI elements without a resource ID: Flutter, Compose and WebView nodes rarely have one."""

    resource_id: str = ""
    text: str | None = None
    text_contains: str | None = None
    description: str | None = None
    description_contains: str | None = None
    role: str | None = None
    checked: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_id, str):
            raise ValueError("resource_id must be a string")
        for name in ("text", "text_contains", "description", "description_contains", "role"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if self.checked is not None and type(self.checked) is not bool:
            raise ValueError("checked must be a boolean")
        if any(not normalize(v) for v in (self.text_contains, self.description_contains, self.role) if v is not None):
            raise ValueError("text_contains, description_contains and role must not be blank")
        if not (self.resource_id or self.text is not None or self.text_contains or self.description is not None
                or self.description_contains or self.role):
            raise ValueError("A selector needs resource_id, text, text_contains, description, description_contains or role")

    @classmethod
    def from_dict(cls, data: Any) -> Selector:
        if not isinstance(data, dict) or any(key not in SELECTOR_FIELDS for key in data):
            raise ValueError(f"Selector fields are {', '.join(SELECTOR_FIELDS)}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    def matches(self, element: Element) -> bool:
        return ((not self.resource_id or element.resource_id == self.resource_id)
                and (self.text is None or element.text == self.text)
                and (self.text_contains is None or normalize(self.text_contains) in normalize(element.text))
                and (self.description is None or element.description == self.description)
                and (self.description_contains is None
                     or normalize(self.description_contains) in normalize(element.description))
                and (self.role is None
                     or normalize(self.role) in (normalize(element.role), normalize(role_name(element.role))))
                and (self.checked is None or element.checked == self.checked))

    def find(self, elements: Iterable[Element]) -> list[Element]:
        return [e for e in elements if self.matches(e)]


@dataclass(frozen=True)
class Assertion:
    package: str
    resource_id: str = ""
    text: str | None = None
    checked: bool | None = None
    text_contains: str | None = None
    description: str | None = None
    description_contains: str | None = None
    role: str | None = None
    absent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.package, str) or not self.package:
            raise ValueError("Assertion package must be a non-empty string")
        if type(self.absent) is not bool:
            raise ValueError("absent must be a boolean")
        self.selector  # Validates the identifying fields.

    @classmethod
    def from_dict(cls, data: Any) -> Assertion:
        allowed = {"package", "absent", *SELECTOR_FIELDS}
        if not isinstance(data, dict) or any(key not in allowed for key in data):
            raise ValueError(f"Assertion fields are {', '.join(sorted(allowed))}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {"package": self.package, **self.selector.to_dict(), **({"absent": True} if self.absent else {})}

    @property
    def selector(self) -> Selector:
        return Selector(self.resource_id, self.text, self.text_contains, self.description,
                        self.description_contains, self.role, self.checked)

    def matches(self, snapshot: Snapshot) -> list[Element]:
        return self.selector.find(e for e in snapshot.elements if e.package == self.package)

    def satisfied(self, snapshot: Snapshot) -> bool:
        count = len(self.matches(snapshot))
        return count == 0 if self.absent else count == 1


@dataclass(frozen=True)
class Input:
    """Caller-supplied text for the one editable field the target selector identifies."""

    target: Selector
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, Selector):
            raise ValueError("Input target must be a selector")
        if not isinstance(self.text, str) or len(self.text) > 2000:
            raise ValueError("Input text must be a string of at most 2000 characters")

    @classmethod
    def from_dict(cls, data: Any) -> Input:
        if not isinstance(data, dict) or set(data) != {"target", "text"}:
            raise ValueError("Each input is an object with target (selector) and text")
        return cls(Selector.from_dict(data["target"]), data["text"])

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target.to_dict(), "text": self.text}


@dataclass(frozen=True)
class Task:
    goal: str
    allowed_packages: tuple[str, ...]
    assertions: tuple[Assertion, ...]
    text_values: dict[str, str] = field(default_factory=dict)
    allow_back: bool = False
    inputs: tuple[Input, ...] = ()

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
        if any(not isinstance(i, Input) for i in self.inputs):
            raise ValueError("inputs must be Input objects")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"goal": self.goal, "allowed_packages": list(self.allowed_packages),
                                "assertions": [a.to_dict() for a in self.assertions]}
        if self.text_values:
            data["text_values"] = dict(self.text_values)
        if self.inputs:
            data["inputs"] = [i.to_dict() for i in self.inputs]
        if self.allow_back:
            data["allow_back"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Task:
        if not isinstance(data, dict) or not isinstance(data.get("allowed_packages"), list) or not isinstance(data.get("assertions"), list):
            raise ValueError("Task requires allowed_packages and assertions arrays")
        if not isinstance(data.get("text_values", {}), dict) or not isinstance(data.get("inputs", []), list):
            raise ValueError("text_values must be an object and inputs a list")
        return cls(goal=data["goal"], allowed_packages=tuple(data["allowed_packages"]),
                   assertions=tuple(Assertion.from_dict(a) for a in data["assertions"]),
                   text_values=data.get("text_values", {}), allow_back=data.get("allow_back", False),
                   inputs=tuple(Input.from_dict(i) for i in data.get("inputs", [])))


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


def input_targets(snapshot: Snapshot, task: Task) -> tuple[dict[str, str], list[str]]:
    """Map editable element IDs to caller text. An input matching several fields sets nothing and is reported."""
    fields = [e for e in snapshot.elements if "set_text" in e.actions and e.enabled and not e.password
              and e.package in task.allowed_packages]
    targets = {e.id: task.text_values[e.resource_id] for e in fields
               if e.resource_id and e.resource_id in task.text_values}
    ambiguous = []
    for item in task.inputs:
        matches = item.target.find(fields)
        if len(matches) == 1:
            targets[matches[0].id] = item.text
        elif matches:
            ambiguous.append(f"Input {json.dumps(item.target.to_dict(), ensure_ascii=False)} matched "
                             f"{len(matches)} fields: " + "; ".join(brief(m) for m in matches[:5]))
    return targets, ambiguous


def nearest(assertion: Assertion, snapshot: Snapshot, limit: int = 5) -> list[Element]:
    """Elements a caller should look at to fix an unmet assertion: the surplus matches, or lookalikes."""
    scoped = [e for e in snapshot.elements if e.package == assertion.package]
    matches = assertion.selector.find(scoped)
    if matches:
        return matches[:limit]
    wanted = [normalize(v) for v in (assertion.text, assertion.text_contains, assertion.description,
                                     assertion.description_contains) if v]
    tokens = re.findall(r"\w+", wanted[0]) if wanted else []
    key = tokens[0] if tokens else ""
    short_id = assertion.resource_id.rsplit("/", 1)[-1]

    def score(e: Element) -> int:
        points = 0
        if short_id and e.resource_id.rsplit("/", 1)[-1] == short_id:
            points += 3
        if key and (key in normalize(e.text) or key in normalize(e.description)):
            points += 2
        if assertion.role and normalize(assertion.role) in (normalize(e.role), normalize(role_name(e.role))):
            points += 1
        return points

    return sorted((e for e in scoped if score(e)), key=score, reverse=True)[:limit]


def candidates(snapshot: Snapshot, task: Task) -> tuple[Action, ...]:
    if snapshot.truncated:
        raise KrilinError("UI tree exceeds the observation limit; narrow the task or driver scope")
    if snapshot.active_package and snapshot.active_package not in task.allowed_packages:
        raise KrilinError(f"Foreground package is outside task scope: {snapshot.active_package}")
    actions = [Action("wait", "wait"), Action("escalate", "escalate")]
    if task.allow_back:
        actions.append(Action("back", "back"))
    targets, _ = input_targets(snapshot, task)
    for e in snapshot.elements:
        if not e.enabled or e.password or e.package not in task.allowed_packages:
            continue
        for kind in e.actions:
            text = None
            if kind == "set_text":
                text = targets.get(e.id)
                if text is None or text == e.text:
                    continue
            actions.append(Action(f"{kind}:{e.id}", kind, e.id, text))
    if len(actions) > 255:
        raise KrilinError("More than 255 action candidates; narrow the task or driver scope")
    return tuple(actions)
