from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from threading import Lock

from .cli import configured_decider
from .device import configured_serial, device_lock, launch, load_driver
from .models import Assertion, Input, Task
from .runner import Limits, Runner
from .scenario import Scenario, run_scenario


def create_server(config: Path, provider: str, model: str | None, env_file: Path):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ValueError('Install MCP support with: pip install -e ".[mcp]"') from exc
    server = FastMCP("Krilin", instructions=(
        "Krilin drives Android apps through the accessibility tree; Jev chooses every UI action from live "
        "candidates and code verifies your assertions. Call android_observe to see elements and write "
        "selectors. Describe a test as android_run_scenario: a launch precondition plus several short "
        "steps, each with one goal and its own assertions; long multi-action goals lower confidence. "
        "Selectors combine resource_id, text, text_contains, description, description_contains, role and "
        "checked; widgets without resource IDs (Flutter, Compose, web) are selected by description or text. "
        "A positive assertion needs exactly one visible match; absent: true needs none. Text is entered "
        "only through inputs/text_values; Jev never invents text. Execution is semantic; success does not "
        "certify TalkBack gestures or speech. An escalated result carries diagnostics (unmet assertions "
        "with nearest elements, ambiguous inputs, candidate count): fix the step or split it, then rerun."
    ))
    lock = Lock()

    @server.tool()
    def android_observe() -> dict:
        """Read current Android UI elements, foreground package and input mode; no model call."""
        with lock, device_lock(config):
            return load_driver(config).observe().state()

    @server.tool()
    def android_run(goal: str, allowed_packages: list[str], assertions: list[dict],
                    text_values: dict[str, str] | None = None, inputs: list[dict] | None = None,
                    max_steps: int = 20, max_seconds: float = 60, allow_back: bool = False) -> dict:
        """Run a bounded subgoal through Jev and return verified success or an escalation with live state.

        Selector fields (any combination): resource_id, text (exact), text_contains, description (exact),
        description_contains, role (e.g. Button, EditText), checked. *_contains ignore case and whitespace.
        Assertions are a selector plus package; a positive assertion needs exactly one visible match,
        "absent": true needs none. Widgets without resource IDs (Flutter, Compose, web) are selected by
        description or text. inputs: [{"target": <selector>, "text": "..."}] sets a field's text;
        text_values: {resource_id: text} is the shorthand for fields that have IDs.
        """
        task = Task(goal, tuple(allowed_packages), tuple(Assertion.from_dict(a) for a in assertions),
                    text_values or {}, allow_back, tuple(Input.from_dict(i) for i in inputs or []))
        with lock, device_lock(config):
            decider = configured_decider(provider, model, env_file)
            try:
                return asdict(Runner(load_driver(config), decider,
                                     Limits(max_steps=max_steps, max_seconds=max_seconds)).run(task))
            finally:
                decider.close()

    @server.tool()
    def android_run_scenario(scenario: dict) -> dict:
        """Run ordered subgoals as one test. scenario = {"name", "launch": {"package", "activity", "clear_task"},
        "allowed_packages", "steps": [{"goal", "assertions", "inputs", "text_values", "allow_back",
        "max_steps", "max_seconds"}], "max_seconds"}. Code launches the app first (clear_task resets it);
        steps run in order and stop at the first escalation. Returns per-step results and failed_step
        (0 = launch failed). Prefer several short steps with their own assertions over one long goal.
        """
        parsed = Scenario.from_dict(scenario)
        with lock, device_lock(config):
            serial = configured_serial(config)
            decider = configured_decider(provider, model, env_file)
            try:
                return asdict(run_scenario(parsed, load_driver(config), decider,
                                           lambda l: launch(serial, l.package, l.activity, l.clear_task)))
            finally:
                decider.close()

    return server


def serve(config: Path, provider: str, model: str | None, env_file: Path) -> None:
    create_server(config, provider, model, env_file).run(transport="stdio")
