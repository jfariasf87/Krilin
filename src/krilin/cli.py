from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from .demo import DemoDecider, DemoDriver, demo_task
from .device import configured_serial, device_lock, launch, load_driver, setup
from .jev import JevDecider
from .models import KrilinError, Task, brief
from .runner import Limits, Runner
from .scenario import Scenario, run_scenario


def configured_decider(provider: str, model: str | None, env_file: Path) -> JevDecider:
    from dotenv import load_dotenv
    load_dotenv(env_file, override=False)
    variable = "OPENROUTER_API_KEY" if provider == "openrouter" else "TYPESAFE_API_KEY"
    key = os.getenv(variable, "")
    if not key:
        raise KrilinError(f"Set {variable} in {env_file} or the environment before using Jev")
    return JevDecider(key, provider, model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded Android UI control for coding agents")
    parser.add_argument("--bridge-config", type=Path, default=Path(".local/bridge.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run the offline deterministic fixture; no device or API key")
    install = sub.add_parser("setup", help="Install the companion and enable it alongside existing services")
    install.add_argument("--serial", required=True)
    install.add_argument("--apk", type=Path, default=Path("android/app/build/outputs/apk/debug/app-debug.apk"))
    observe = sub.add_parser("observe", help="Read a structured live snapshot without a model")
    observe.add_argument("--brief", action="store_true", help="One line per element with the fields a selector needs")
    observe.add_argument("--package", help="Show only elements of this package")
    sub.add_parser("smoke", help="Run the companion's isolated demo screen without a model")
    for command in ("run", "serve"):
        run = sub.add_parser(command, help="Run a task with Jev" if command == "run" else "Expose tools over MCP stdio")
        if command == "run":
            target = run.add_mutually_exclusive_group(required=True)
            target.add_argument("--task", type=Path, help="One bounded subgoal (JSON)")
            target.add_argument("--scenario", type=Path, help="Ordered steps with an optional launch precondition (JSON)")
            run.add_argument("--trace", type=Path)
            run.add_argument("--record", action="store_true", help="Write every observation into the trace for replay")
            run.add_argument("--max-steps", type=int, default=20)
            run.add_argument("--max-seconds", type=float, default=60)
        run.add_argument("--provider", choices=("openrouter", "typesafe"), default="openrouter")
        run.add_argument("--model")
        run.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")  # UI text is arbitrary Unicode; consoles are not.
    try:
        if args.command == "demo":
            result = Runner(DemoDriver(), DemoDecider()).run(demo_task())
        elif args.command == "setup":
            with device_lock(args.bridge_config):
                setup(args.serial, args.apk, args.bridge_config)
            print(json.dumps({"status": "configured", "config": str(args.bridge_config)}))
            return
        elif args.command == "serve":
            from .mcp_server import serve
            serve(args.bridge_config, args.provider, args.model, args.env_file)
            return
        else:
            with device_lock(args.bridge_config):
                driver = load_driver(args.bridge_config)
                if args.command == "observe":
                    snapshot = driver.observe()
                    elements = [e for e in snapshot.elements if not args.package or e.package == args.package]
                    if args.brief:
                        print(f"package={snapshot.active_package} elements={len(elements)} truncated={snapshot.truncated} "
                              f"ime_visible={snapshot.input.ime_visible} touch_exploration={snapshot.input.touch_exploration}")
                        for element in elements:
                            prefix = "" if element.package == snapshot.active_package else f"[{element.package}] "
                            print(prefix + brief(element))
                        return
                    state = snapshot.state()
                    state["elements"] = [e for e in state["elements"] if not args.package or e["package"] == args.package]
                    print(json.dumps(state, indent=2))
                    return
                if args.command == "smoke":
                    launch(configured_serial(args.bridge_config), "dev.krilin.bridge", ".DemoActivity")
                    result = Runner(driver, DemoDecider()).run(demo_task())
                elif args.scenario:
                    scenario = Scenario.from_dict(json.loads(args.scenario.read_text(encoding="utf-8")))
                    serial = configured_serial(args.bridge_config)
                    decider = configured_decider(args.provider, args.model, args.env_file)
                    try:
                        result = run_scenario(scenario, driver, decider, trace=args.trace, record=args.record,
                                              launcher=lambda l: launch(serial, l.package, l.activity, l.clear_task))
                    finally:
                        decider.close()
                else:
                    task = Task.from_dict(json.loads(args.task.read_text(encoding="utf-8")))
                    decider = configured_decider(args.provider, args.model, args.env_file)
                    try:
                        result = Runner(driver, decider, Limits(max_steps=args.max_steps, max_seconds=args.max_seconds),
                                        args.trace, record=args.record).run(task)
                    finally:
                        decider.close()
        print(json.dumps(asdict(result), indent=2))
        if result.status != "succeeded":
            sys.exit(2)
    except (KrilinError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)
