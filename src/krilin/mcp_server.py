from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from threading import Lock

from .cli import configured_decider
from .device import device_lock, load_driver
from .models import Assertion, Task
from .runner import Limits, Runner


def create_server(config: Path, provider: str, model: str | None, env_file: Path):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ValueError('Install MCP support with: pip install -e ".[mcp]"') from exc
    server = FastMCP("Krilin", instructions=(
        "Delegate one concrete Android UI subgoal at a time. Observe first, then provide "
        "allowed packages, exact UI success assertions and any text to enter. "
        "Execution is semantic; success does not certify TalkBack gestures or speech. "
        "An escalated result requires inspecting the returned state before replanning."
    ))
    lock = Lock()

    @server.tool()
    def android_observe() -> dict:
        """Read current Android UI elements, foreground package and input mode; no model call."""
        with lock, device_lock(config):
            return load_driver(config).observe().state()

    @server.tool()
    def android_run(goal: str, allowed_packages: list[str], assertions: list[dict],
                    text_values: dict[str, str] | None = None,
                    max_steps: int = 20, max_seconds: float = 60, allow_back: bool = False) -> dict:
        """Run a bounded subgoal through Jev. Assertions: package plus resource_id and/or exact text,
        optionally checked. text_values maps resource IDs to exact replacement strings.
        Returns verified success or an escalation with live state and recent history.
        """
        task = Task(goal, tuple(allowed_packages), tuple(Assertion(**a) for a in assertions),
                    text_values or {}, allow_back)
        with lock, device_lock(config):
            decider = configured_decider(provider, model, env_file)
            try:
                return asdict(Runner(load_driver(config), decider,
                                     Limits(max_steps=max_steps, max_seconds=max_seconds)).run(task))
            finally:
                decider.close()

    return server


def serve(config: Path, provider: str, model: str | None, env_file: Path) -> None:
    create_server(config, provider, model, env_file).run(transport="stdio")
