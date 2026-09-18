"""Deterministic local fixture. Deliberately not a simulation of Jev quality."""

from .models import Action, Decision, Element, InputState, Snapshot, Task, Assertion

PACKAGE = "dev.krilin.bridge"


def demo_task() -> Task:
    return Task("Enter Krilin in Name, then Save", (PACKAGE,),
                (Assertion(PACKAGE, f"{PACKAGE}:id/demo_status", "Saved: Krilin"),),
                {f"{PACKAGE}:id/demo_name": "Krilin"})


class DemoDecider:
    def decide(self, state: dict, actions: tuple[Action, ...], timeout: float) -> Decision:
        name = next((e for e in state["ui"]["elements"] if e["resource_id"].endswith("/demo_name")), None)
        save = next((e for e in state["ui"]["elements"] if e["resource_id"].endswith("/demo_save")), None)
        action_id = "escalate"
        if not name or not save:
            action_id = "wait"
        elif name["text"] != "Krilin":
            action_id = f"set_text:{name['id']}"
        elif save:
            action_id = f"click:{save['id']}"
        if action_id not in {a.id for a in actions}:
            action_id = "escalate"
        return Decision(action_id, 1, 1, 0, "fixture-only/no-model")


class DemoDriver:
    def __init__(self) -> None:
        self.name = ""
        self.saved = False
        self.version = 0

    def observe(self, timeout: float) -> Snapshot:
        return Snapshot(str(self.version), (
            Element("e1", PACKAGE, f"{PACKAGE}:id/demo_name", self.name, actions=("set_text",)),
            Element("e2", PACKAGE, f"{PACKAGE}:id/demo_save", "Save", actions=("click",)),
            Element("e3", PACKAGE, f"{PACKAGE}:id/demo_status", f"Saved: {self.name}" if self.saved else "Not saved"),
        ), InputState(), PACKAGE)

    def execute(self, snapshot: Snapshot, action: Action, timeout: float) -> None:
        if action.kind == "set_text":
            self.name = action.text or ""
        elif action.kind == "click":
            self.saved = True
        self.version += 1
